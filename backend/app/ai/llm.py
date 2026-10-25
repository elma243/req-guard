"""Provider-agnostic LLM client (CLAUDE.md rule 3). Every LLM call in the app goes through here.

- `complete_json(prompt_name, variables, schema)` loads `app/ai/prompts/<prompt_name>.md`, fills in
  `{{variable}}` placeholders, asks for JSON only, validates the answer with the Pydantic `schema`,
  retries once when the answer is invalid, and raises `LLMError` with a clear message after that.
- Providers only turn a prompt into text. Add a new provider as one more class in `PROVIDERS`.
- Optional per call: `thinking` (a reasoning level hint, e.g. "low") and `deadline` (a `clock()` time by
  which the call must end; HTTP attempts and retries are cut to fit, so a request has a known maximum).
- Logged per HTTP attempt and per call: provider, model, attempt, status, tokens and elapsed time.
  Never the API key, and never the prompt or answer text (it contains client messages).
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Overloaded (503) and rate limit (429) are retried; other errors (400, 401, 403, ...) are not
RETRY_STATUSES = (429, 503)
# Waits before retry 1, 2 and 3 when the response has no usable Retry-After header
BACKOFF_SECONDS = (2.0, 4.0, 8.0)
# A longer Retry-After is not worth blocking the request for; the user can try again later
MAX_WAIT_SECONDS = 60.0
# No HTTP attempt is started with less time than this left before the deadline
MIN_ATTEMPT_SECONDS = 5.0

# Replaced in tests so retries do not wait and deadlines can be simulated
sleep = time.sleep
clock = time.monotonic


def now() -> float:
    """Current `clock()` time; deadlines are given in this clock."""
    return clock()


def thinking_level(value: str | None) -> str | None:
    """A configured thinking level, or None for "default" / empty (then the model decides)."""
    level = (value or "").strip().lower()
    return None if level in ("", "default") else level

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """The LLM call failed in a way the user can understand (bad key, rate limit, invalid answer...)."""


class LLMNotConfiguredError(LLMError):
    """LLM_PROVIDER, LLM_MODEL or LLM_API_KEY is missing or unsupported."""


@dataclass
class LLMResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    def generate(self, prompt: str, *, thinking: str | None = None, deadline: float | None = None) -> LLMResponse: ...


class GeminiProvider:
    """Google Gemini through its REST API (generateContent). The key is sent in a header, never in the URL.

    HTTP 503 (overloaded) and 429 (rate limit) are retried up to 3 times after 2, 4 and 8 seconds, or after
    the delay Gemini asks for (Retry-After header or RetryInfo.retryDelay in the body). A 429 for the daily
    quota (or a delay over MAX_WAIT_SECONDS) fails at once. Other errors fail at once. `thinking` is sent as
    generationConfig.thinkingConfig.thinkingLevel.
    """

    name: ClassVar[str] = "gemini"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, model: str, api_key: str, client: httpx.Client | None = None, timeout: float = 120.0):
        self.model = model
        self._api_key = api_key
        self.timeout = timeout
        self.client = client or httpx.Client()

    def _safe(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def _log_attempt(self, attempt: int, outcome: object, started: float) -> None:
        logger.info(
            "llm http provider=gemini model=%s attempt=%d status=%s elapsed_ms=%d",
            self.model,
            attempt,
            outcome,
            (time.perf_counter() - started) * 1000,
        )

    def _post(self, body: dict, deadline: float | None) -> httpx.Response:
        """POST with retries on 429/503: up to len(BACKOFF_SECONDS) times, waiting Retry-After or the backoff.

        Each attempt waits at most `self.timeout` seconds, and never past the deadline.
        """
        url = f"{self.BASE_URL}/models/{self.model}:generateContent"
        retries = 0
        while True:
            timeout = self.timeout
            if deadline is not None:
                timeout = min(timeout, deadline - now())
                if timeout < MIN_ATTEMPT_SECONDS:
                    raise LLMError("Gemini did not answer in time. Try again later.")
            started = time.perf_counter()
            try:
                response = self.client.post(
                    url, headers={"x-goog-api-key": self._api_key}, json=body, timeout=httpx.Timeout(timeout)
                )
            except httpx.TimeoutException:
                self._log_attempt(retries + 1, "timeout", started)
                raise LLMError(f"Gemini did not answer within {timeout:.0f} seconds. Try again later.") from None
            except httpx.HTTPError:
                self._log_attempt(retries + 1, "network_error", started)
                raise LLMError("Could not reach Gemini. Check the internet connection and settings.") from None
            self._log_attempt(retries + 1, response.status_code, started)

            if response.status_code not in RETRY_STATUSES:
                return response
            delay = retry_delay(response)
            if response.status_code == 429 and (is_daily_quota(response) or (delay or 0) > MAX_WAIT_SECONDS):
                logger.warning("llm provider=gemini daily quota used up, retry_delay_s=%s; not retrying", delay)
                raise LLMError(daily_quota_message(delay))
            if retries == len(BACKOFF_SECONDS):
                return response
            wait = BACKOFF_SECONDS[retries] if delay is None else delay
            if wait > MAX_WAIT_SECONDS:
                return response
            if deadline is not None and now() + wait + MIN_ATTEMPT_SECONDS > deadline:
                return response  # no time left for another attempt
            retries += 1
            logger.warning("llm provider=gemini status=%d retry=%d wait_s=%.1f", response.status_code, retries, wait)
            sleep(wait)

    def generate(self, prompt: str, *, thinking: str | None = None, deadline: float | None = None) -> LLMResponse:
        config: dict[str, Any] = {"responseMimeType": "application/json", "temperature": 0}
        if thinking:
            config["thinkingConfig"] = {"thinkingLevel": thinking}
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
        response = self._post(body, deadline)
        try:
            data = response.json()
        except ValueError:
            data = None

        status = response.status_code
        if status >= 400:
            detail = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
            hint = f": {detail}" if detail else ""
            if status in RETRY_STATUSES:
                problem = "is overloaded" if status == 503 else "rate limit reached"
                raise LLMError(self._safe(f"Gemini {problem} (HTTP {status}){hint}. Try again in a few minutes."))
            raise LLMError(self._safe(f"Gemini returned an error (HTTP {status}){hint}"))
        if not isinstance(data, dict):
            raise LLMError("Gemini returned a response that is not JSON.")

        candidates = data.get("candidates") or []
        parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            reason = (data.get("promptFeedback") or {}).get("blockReason") or (
                candidates[0].get("finishReason") if candidates else None
            )
            raise LLMError(f"Gemini returned no answer{f' ({reason})' if reason else ''}.")

        usage = data.get("usageMetadata") or {}
        output = usage.get("candidatesTokenCount")
        if output is not None and usage.get("thoughtsTokenCount"):
            output += usage["thoughtsTokenCount"]
        return LLMResponse(text=text, input_tokens=usage.get("promptTokenCount"), output_tokens=output)


_RETRY_DELAY = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$")


def _error(response: httpx.Response) -> dict:
    """The "error" object of a Gemini error body, or {} when the body is not that."""
    try:
        data = response.json()
    except ValueError:
        return {}
    error = data.get("error") if isinstance(data, dict) else None
    return error if isinstance(error, dict) else {}


def _details(response: httpx.Response) -> list[dict]:
    return [d for d in (_error(response).get("details") or []) if isinstance(d, dict)]


def retry_delay(response: httpx.Response) -> float | None:
    """Seconds Gemini asks us to wait: the Retry-After header, else error.details RetryInfo.retryDelay
    (e.g. "44953s"). None when neither is given or usable."""
    try:
        seconds = float(response.headers.get("Retry-After", "").strip())
        if seconds >= 0:
            return seconds
    except ValueError:
        pass
    for detail in _details(response):
        if str(detail.get("@type", "")).endswith("google.rpc.RetryInfo"):
            match = _RETRY_DELAY.match(str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def is_daily_quota(response: httpx.Response) -> bool:
    """True when the 429 says a per-day quota was exceeded (in the message or a QuotaFailure quotaId)."""
    message = str(_error(response).get("message", "")).lower()
    if "per day" in message or "perday" in message:
        return True
    return any(
        "perday" in str(violation.get("quotaId", "")).lower()
        for detail in _details(response)
        if str(detail.get("@type", "")).endswith("google.rpc.QuotaFailure")
        for violation in (detail.get("violations") or [])
        if isinstance(violation, dict)
    )


def daily_quota_message(delay: float | None) -> str:
    if delay is None:
        return "Gemini daily free quota used up. Try again tomorrow."
    hours = max(1, round(delay / 3600))
    return f"Gemini daily free quota used up. Try again in about {hours} hour{'' if hours == 1 else 's'}."


PROVIDERS: dict[str, type] = {"gemini": GeminiProvider}


# ---------- prompts ----------

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt not found: app/ai/prompts/{name}.md")
    return path.read_text(encoding="utf-8")


def render_prompt(name: str, variables: dict[str, Any], schema: type[BaseModel]) -> str:
    """Prompt text with `{{name}}` placeholders filled in and the JSON output format appended.

    Strings are inserted as they are; other values as JSON (non-ASCII kept, so Bangla stays readable).
    """

    def fill(match: re.Match) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"Prompt '{name}' needs the variable '{key}'")
        value = variables[key]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)

    text = _PLACEHOLDER.sub(fill, load_prompt(name)).rstrip()
    schema_json = json.dumps(schema.model_json_schema(), indent=1)
    return (
        f"{text}\n\n## Output format\n"
        "Answer with one JSON object only, with no other text before or after it. "
        f"It must match this JSON Schema:\n```json\n{schema_json}\n```\n"
    )


# ---------- answers ----------

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class InvalidAnswer(Exception):
    """The answer is not JSON, or does not match the schema. The message holds no answer text."""


def parse_answer(text: str, schema: type[T]) -> T:
    cleaned = text.strip()
    fenced = _FENCE.match(cleaned)
    if fenced:
        cleaned = fenced.group(1)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise InvalidAnswer(f"not valid JSON ({exc.msg} at position {exc.pos})") from None
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'answer'}: {e['msg']}" for e in exc.errors()[:5])
        raise InvalidAnswer(f"does not match the schema ({problems})") from None


RETRY_NOTE = (
    "\n\n## Your previous answer was rejected\n"
    "It was {reason}. Answer again with one JSON object only that matches the schema above.\n"
)


class LLMClient:
    """Asks a provider for JSON and validates it. Tests replace this with a fake."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def complete_json(
        self,
        prompt_name: str,
        variables: dict[str, Any],
        schema: type[T],
        *,
        thinking: str | None = None,
        deadline: float | None = None,
    ) -> T:
        prompt = render_prompt(prompt_name, variables, schema)
        attempt_prompt = prompt
        for attempt in (1, 2):
            if attempt == 2 and deadline is not None and now() + MIN_ATTEMPT_SECONDS > deadline:
                raise LLMError("The AI returned an invalid answer and there was no time left to ask again. Try again.")
            response = self._call(prompt_name, attempt_prompt, attempt, thinking, deadline)
            try:
                return parse_answer(response.text, schema)
            except InvalidAnswer as exc:
                logger.warning("llm invalid answer prompt=%s attempt=%d: %s", prompt_name, attempt, exc)
                attempt_prompt = prompt + RETRY_NOTE.format(reason=exc)
        raise LLMError("The AI returned an invalid answer twice. Try again; if it keeps failing, try another model.")

    def _call(
        self, prompt_name: str, prompt: str, attempt: int, thinking: str | None, deadline: float | None
    ) -> LLMResponse:
        """One call to the provider (its HTTP retries included), logged with the elapsed time."""
        started = time.perf_counter()
        try:
            response = self.provider.generate(prompt, thinking=thinking, deadline=deadline)
        except LLMError:
            logger.warning(
                "llm call failed prompt=%s provider=%s model=%s attempt=%d thinking=%s elapsed_ms=%d",
                prompt_name,
                self.provider.name,
                self.provider.model,
                attempt,
                thinking or "default",
                (time.perf_counter() - started) * 1000,
            )
            raise
        logger.info(
            "llm call prompt=%s provider=%s model=%s attempt=%d thinking=%s input_tokens=%s output_tokens=%s "
            "elapsed_ms=%d",
            prompt_name,
            self.provider.name,
            self.provider.model,
            attempt,
            thinking or "default",
            response.input_tokens,
            response.output_tokens,
            (time.perf_counter() - started) * 1000,
        )
        return response


def get_llm() -> LLMClient:
    """The LLM client configured in backend/.env. Errors name the missing variables, never their values."""
    missing = [
        name
        for name, value in (
            ("LLM_PROVIDER", settings.llm_provider),
            ("LLM_MODEL", settings.llm_model),
            ("LLM_API_KEY", settings.llm_api_key),
        )
        if not (value or "").strip()
    ]
    if missing:
        raise LLMNotConfiguredError(f"The LLM is not configured. Set {', '.join(missing)} in backend/.env.")
    provider_name = settings.llm_provider.strip().lower()
    provider_cls = PROVIDERS.get(provider_name)
    if provider_cls is None:
        raise LLMNotConfiguredError(
            f"LLM_PROVIDER '{provider_name}' is not supported. Supported: {', '.join(sorted(PROVIDERS))}."
        )
    return LLMClient(
        provider_cls(
            model=settings.llm_model.strip(),
            api_key=settings.llm_api_key.strip(),
            timeout=settings.llm_timeout_seconds,
        )
    )


def complete_json(prompt_name: str, variables: dict[str, Any], schema: type[T], **options: Any) -> T:
    """Shortcut for `get_llm().complete_json(...)`."""
    return get_llm().complete_json(prompt_name, variables, schema, **options)
