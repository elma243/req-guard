"""Provider-agnostic LLM client (CLAUDE.md rule 3). Every LLM call in the app goes through here.

- `complete_json(prompt_name, variables, schema)` loads `app/ai/prompts/<prompt_name>.md`, fills in
  `{{variable}}` placeholders, asks for JSON only, validates the answer with the Pydantic `schema`,
  retries once when the answer is invalid, and raises `LLMError` with a clear message after that.
- Providers only turn a prompt into text: Gemini (generateContent) and Groq (OpenAI-compatible chat
  completions, JSON mode). Add a new provider as one more class and one more entry in `CHAIN_STEPS`.
- Optional per call: `thinking` (a reasoning level hint, e.g. "low") and `deadline` (a `clock()` time by
  which the call must end; HTTP attempts and retries are cut to fit, so a request has a known maximum).
- Provider chain (LLM_CHAIN, default "groq,gemini"): the providers are asked in that order; "gemini" means
  LLM_MODEL and then LLM_FALLBACK_MODEL. The next one is asked only when a provider is unavailable
  (overloaded or rate limited after its retries, timed out, or daily quota used up); other errors (bad key,
  bad request) are raised at once. An unavailable provider is skipped for the rest of the client's calls
  (one extraction run), so later batches do not wait for it again. `last_model` is "provider:model" of the
  model that answered, e.g. "groq:openai/gpt-oss-120b".
- Logged per HTTP attempt and per call: provider, model, attempt, status, tokens and elapsed time.
  Never the API key, and never the prompt or answer text (it contains client messages).
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Protocol, TypeVar

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
    """LLM_CHAIN names an unknown provider, or no provider in it has its model and key set."""


class LLMUnavailableError(LLMError):
    """The model is busy (503 / 429 after retries), timed out, or its daily quota is used up.
    Another model may still answer, so the next provider in the chain is tried."""


ALL_UNAVAILABLE = "All AI models are busy or out of free quota. Try again later."


@dataclass
class LLMResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    def generate(self, prompt: str, *, thinking: str | None = None, deadline: float | None = None) -> LLMResponse: ...


class _HTTPProvider:
    """Shared HTTP part of the providers: one JSON POST with retries, deadlines and safe logging.

    HTTP 503 (overloaded) and 429 (rate limit) are retried up to 3 times after 2, 4 and 8 seconds, or after
    the delay the service asks for (Retry-After header, or RetryInfo.retryDelay in a Gemini body). A 429 for
    the daily quota (or a delay over MAX_WAIT_SECONDS) fails at once. Other errors fail at once.
    `max_retries=0` sends each request only once (Gemini fallback model).
    """

    name: ClassVar[str]
    label: ClassVar[str]  # name in messages for the user, e.g. "Gemini"

    def __init__(
        self,
        model: str,
        api_key: str,
        client: httpx.Client | None = None,
        timeout: float = 120.0,
        max_retries: int = len(BACKOFF_SECONDS),
    ):
        self.model = model
        self._api_key = api_key
        self.timeout = timeout
        self.max_retries = min(max_retries, len(BACKOFF_SECONDS))
        self.client = client or httpx.Client()

    def _url(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _safe(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def _log_attempt(self, attempt: int, outcome: object, started: float) -> None:
        logger.info(
            "llm http provider=%s model=%s attempt=%d status=%s elapsed_ms=%d",
            self.name,
            self.model,
            attempt,
            outcome,
            (time.perf_counter() - started) * 1000,
        )

    def _post(self, body: dict, deadline: float | None) -> httpx.Response:
        """POST with retries on 429/503: up to `max_retries` times, waiting Retry-After or the backoff.

        Each attempt waits at most `self.timeout` seconds, and never past the deadline.
        """
        retries = 0
        while True:
            timeout = self.timeout
            if deadline is not None:
                timeout = min(timeout, deadline - now())
                if timeout < MIN_ATTEMPT_SECONDS:
                    raise LLMError(f"{self.label} did not answer in time. Try again later.")
            started = time.perf_counter()
            try:
                response = self.client.post(
                    self._url(), headers=self._headers(), json=body, timeout=httpx.Timeout(timeout)
                )
            except httpx.TimeoutException:
                self._log_attempt(retries + 1, "timeout", started)
                raise LLMUnavailableError(
                    f"{self.label} did not answer within {timeout:.0f} seconds. Try again later."
                ) from None
            except httpx.HTTPError:
                self._log_attempt(retries + 1, "network_error", started)
                raise LLMError(
                    f"Could not reach {self.label}. Check the internet connection and settings."
                ) from None
            self._log_attempt(retries + 1, response.status_code, started)

            if response.status_code not in RETRY_STATUSES:
                return response
            delay = retry_delay(response)
            if response.status_code == 429 and (is_daily_quota(response) or (delay or 0) > MAX_WAIT_SECONDS):
                logger.warning(
                    "llm provider=%s model=%s daily quota used up, retry_delay_s=%s; not retrying",
                    self.name,
                    self.model,
                    delay,
                )
                raise LLMUnavailableError(daily_quota_message(delay, self.label))
            if retries == self.max_retries:
                return response
            wait = BACKOFF_SECONDS[retries] if delay is None else delay
            if wait > MAX_WAIT_SECONDS:
                return response
            if deadline is not None and now() + wait + MIN_ATTEMPT_SECONDS > deadline:
                return response  # no time left for another attempt
            retries += 1
            logger.warning(
                "llm provider=%s model=%s status=%d retry=%d wait_s=%.1f",
                self.name,
                self.model,
                response.status_code,
                retries,
                wait,
            )
            sleep(wait)

    def _raise_for_status(self, response: httpx.Response) -> None:
        """LLMUnavailableError for a 429/503 left after the retries, LLMError for other 4xx/5xx."""
        status = response.status_code
        if status < 400:
            return
        detail = str(_error(response).get("message", ""))
        hint = f": {detail}" if detail else ""
        if status in RETRY_STATUSES:
            problem = "is overloaded" if status == 503 else "rate limit reached"
            raise LLMUnavailableError(
                self._safe(f"{self.label} {problem} (HTTP {status}){hint}. Try again in a few minutes.")
            )
        raise LLMError(self._safe(f"{self.label} returned an error (HTTP {status}){hint}"))

    def _json(self, response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise LLMError(f"{self.label} returned a response that is not JSON.")
        return data


class GeminiProvider(_HTTPProvider):
    """Google Gemini through its REST API (generateContent). The key is sent in a header, never in the URL.
    `thinking` is sent as generationConfig.thinkingConfig.thinkingLevel."""

    name: ClassVar[str] = "gemini"
    label: ClassVar[str] = "Gemini"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def _url(self) -> str:
        return f"{self.BASE_URL}/models/{self.model}:generateContent"

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key}

    def generate(self, prompt: str, *, thinking: str | None = None, deadline: float | None = None) -> LLMResponse:
        config: dict[str, Any] = {"responseMimeType": "application/json", "temperature": 0}
        if thinking:
            config["thinkingConfig"] = {"thinkingLevel": thinking}
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
        response = self._post(body, deadline)
        self._raise_for_status(response)
        data = self._json(response)

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


class GroqProvider(_HTTPProvider):
    """Groq through its OpenAI-compatible API (chat/completions) in JSON mode, with httpx only.

    The key is sent as a Bearer header. `thinking` is sent as `reasoning_effort` to gpt-oss models (they
    accept low / medium / high; "minimal" is sent as "low"). When Groq's own JSON check fails (HTTP 400
    json_validate_failed), the failed text is returned as the answer, so the client's "invalid answer,
    ask once more" step handles it like any other invalid JSON.
    """

    name: ClassVar[str] = "groq"
    label: ClassVar[str] = "Groq"
    BASE_URL = "https://api.groq.com/openai/v1"

    def _url(self) -> str:
        return f"{self.BASE_URL}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def generate(self, prompt: str, *, thinking: str | None = None, deadline: float | None = None) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if thinking and "gpt-oss" in self.model:
            body["reasoning_effort"] = thinking if thinking in ("low", "medium", "high") else "low"
        response = self._post(body, deadline)
        error = _error(response)
        if response.status_code == 400 and error.get("code") == "json_validate_failed":
            logger.warning("llm provider=groq model=%s answer failed Groq's JSON check", self.model)
            return LLMResponse(text=str(error.get("failed_generation") or ""))
        self._raise_for_status(response)
        data = self._json(response)

        choices = data.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        text = (choice.get("message") or {}).get("content") or ""
        if not text:
            reason = choice.get("finish_reason")
            raise LLMError(f"Groq returned no answer{f' ({reason})' if reason else ''}.")

        usage = data.get("usage") or {}
        return LLMResponse(
            text=text, input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens")
        )


_RETRY_DELAY = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$")


def _error(response: httpx.Response) -> dict:
    """The "error" object of an error body (Gemini and Groq both use one), or {} when the body is not that."""
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
    """True when the 429 says a per-day quota was exceeded (in the message or a Gemini QuotaFailure quotaId).
    Groq says e.g. "Rate limit reached ... on tokens per day (TPD)"."""
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


def daily_quota_message(delay: float | None, label: str = "Gemini") -> str:
    if delay is None:
        return f"{label} daily free quota used up. Try again tomorrow."
    hours = max(1, round(delay / 3600))
    return f"{label} daily free quota used up. Try again in about {hours} hour{'' if hours == 1 else 's'}."


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
    """Asks the providers for JSON, in order, and validates the answer. Tests replace this with a fake.

    A call the current provider cannot serve (LLMUnavailableError) goes to the next provider, and later
    calls of this client skip the unavailable one. Any other LLMError is raised at once.
    """

    # "provider:model" that gave the last valid answer, e.g. to store it with extracted requirements
    last_model: str | None = None

    def __init__(self, *providers: LLMProvider):
        if not providers:
            raise ValueError("LLMClient needs at least one provider")
        self.providers = list(providers)
        self._unavailable: set[int] = set()  # indexes of providers that were unavailable

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
            response, provider = self._generate(prompt_name, attempt_prompt, attempt, thinking, deadline)
            try:
                answer = parse_answer(response.text, schema)
            except InvalidAnswer as exc:
                logger.warning("llm invalid answer prompt=%s attempt=%d: %s", prompt_name, attempt, exc)
                attempt_prompt = prompt + RETRY_NOTE.format(reason=exc)
                continue
            model = f"{provider.name}:{provider.model}"
            if model != self.last_model:
                logger.info("llm answered by provider=%s model=%s", provider.name, provider.model)
            self.last_model = model
            return answer
        raise LLMError("The AI returned an invalid answer twice. Try again; if it keeps failing, try another model.")

    def _generate(
        self, prompt_name: str, prompt: str, attempt: int, thinking: str | None, deadline: float | None
    ) -> tuple[LLMResponse, LLMProvider]:
        """The answer and the provider that gave it: the first provider of the chain that is available."""
        call = (prompt_name, prompt, attempt, thinking, deadline)
        if len(self.providers) == 1:
            return self._call(self.providers[0], 1, *call), self.providers[0]
        unavailable: list[LLMProvider] = []
        for index, provider in enumerate(self.providers):
            if index in self._unavailable:
                unavailable.append(provider)
                continue
            try:
                return self._call(provider, index + 1, *call), provider
            except LLMUnavailableError as exc:
                self._unavailable.add(index)
                unavailable.append(provider)
                logger.warning(
                    "llm provider=%s model=%s unavailable (%s); trying the next model in LLM_CHAIN",
                    provider.name,
                    provider.model,
                    exc,
                )
            except LLMError as exc:
                if not unavailable:
                    raise
                names = ", ".join(p.model for p in unavailable)
                verb = "is" if len(unavailable) == 1 else "are"
                raise LLMError(f"{names} {verb} unavailable and {provider.model} failed: {exc}") from None
        raise LLMUnavailableError(ALL_UNAVAILABLE)

    def _call(
        self,
        provider: LLMProvider,
        step: int,
        prompt_name: str,
        prompt: str,
        attempt: int,
        thinking: str | None,
        deadline: float | None,
    ) -> LLMResponse:
        """One call to a provider (its HTTP retries included), logged with the model and the elapsed time."""
        started = time.perf_counter()
        try:
            response = provider.generate(prompt, thinking=thinking, deadline=deadline)
        except LLMError:
            logger.warning(
                "llm call failed prompt=%s provider=%s model=%s step=%d attempt=%d thinking=%s elapsed_ms=%d",
                prompt_name,
                provider.name,
                provider.model,
                step,
                attempt,
                thinking or "default",
                (time.perf_counter() - started) * 1000,
            )
            raise
        logger.info(
            "llm call prompt=%s provider=%s model=%s step=%d attempt=%d thinking=%s input_tokens=%s "
            "output_tokens=%s elapsed_ms=%d",
            prompt_name,
            provider.name,
            provider.model,
            step,
            attempt,
            thinking or "default",
            response.input_tokens,
            response.output_tokens,
            (time.perf_counter() - started) * 1000,
        )
        return response


# ---------- configuration ----------


def _value(setting: str | None) -> str:
    return (setting or "").strip()


def _groq_steps(timeout: float) -> tuple[list[LLMProvider], list[str]]:
    key, model = _value(settings.groq_api_key), _value(settings.groq_model)
    missing = [name for name, value in (("GROQ_API_KEY", key), ("GROQ_MODEL", model)) if not value]
    if missing:
        return [], missing
    return [GroqProvider(model=model, api_key=key, timeout=timeout)], []


def _gemini_steps(timeout: float) -> tuple[list[LLMProvider], list[str]]:
    """LLM_MODEL with its retries, then LLM_FALLBACK_MODEL (if set) sent once: the main one used the retries."""
    model, key = _value(settings.llm_model), _value(settings.llm_api_key)
    missing = [name for name, value in (("LLM_MODEL", model), ("LLM_API_KEY", key)) if not value]
    if missing:
        return [], missing
    steps: list[LLMProvider] = [GeminiProvider(model=model, api_key=key, timeout=timeout)]
    fallback = _value(settings.llm_fallback_model)
    if fallback and fallback != model:
        steps.append(GeminiProvider(model=fallback, api_key=key, timeout=timeout, max_retries=0))
    return steps, []


# Provider name in LLM_CHAIN -> its providers in order, or the names of its missing .env variables
CHAIN_STEPS: dict[str, Callable[[float], tuple[list[LLMProvider], list[str]]]] = {
    "groq": _groq_steps,
    "gemini": _gemini_steps,
}


def get_llm() -> LLMClient:
    """The LLM client for LLM_CHAIN in backend/.env; a provider without its key or model is skipped.
    Errors name the missing variables, never their values."""
    names = list(dict.fromkeys(n.strip().lower() for n in (settings.llm_chain or "").split(",") if n.strip()))
    supported = ", ".join(CHAIN_STEPS)
    if not names:
        raise LLMNotConfiguredError(f"The LLM is not configured. Set LLM_CHAIN in backend/.env ({supported}).")
    unknown = [n for n in names if n not in CHAIN_STEPS]
    if unknown:
        raise LLMNotConfiguredError(
            f"LLM_CHAIN names unsupported provider(s): {', '.join(unknown)}. Supported: {supported}."
        )
    providers: list[LLMProvider] = []
    missing: list[str] = []
    for name in names:
        steps, needs = CHAIN_STEPS[name](settings.llm_timeout_seconds)
        if needs:
            logger.info("llm chain: skipping provider=%s, %s not set", name, ", ".join(needs))
        providers += steps
        missing += needs
    if not providers:
        raise LLMNotConfiguredError(f"The LLM is not configured. Set {', '.join(missing)} in backend/.env.")
    return LLMClient(*providers)


def complete_json(prompt_name: str, variables: dict[str, Any], schema: type[T], **options: Any) -> T:
    """Shortcut for `get_llm().complete_json(...)`."""
    return get_llm().complete_json(prompt_name, variables, schema, **options)
