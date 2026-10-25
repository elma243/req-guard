"""LLM client: prompt loading, JSON validation with one retry, Gemini over mocked HTTP. No real API calls."""

import json
import logging
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel

from app.ai import llm
from app.ai.llm import GeminiProvider, LLMClient, LLMError, LLMNotConfiguredError, LLMResponse
from app.core.config import settings

FAKE_KEY = "AIza-test-key-0123456789"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
LLM_FIXTURES = Path(__file__).parent / "fixtures" / "llm"


def gemini_fixture(name: str):
    return json.loads((LLM_FIXTURES / name).read_text(encoding="utf-8"))


class Answer(BaseModel):
    answer: str
    score: float


class ScriptedProvider:
    """Returns the given answers in order and remembers the prompts."""

    name = "scripted"
    model = "test-model"

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.options: list[dict] = []

    def generate(self, prompt: str, **options) -> LLMResponse:
        self.prompts.append(prompt)
        self.options.append(options)
        return LLMResponse(text=self.answers.pop(0), input_tokens=10, output_tokens=5)


@pytest.fixture
def prompts_dir(tmp_path, monkeypatch):
    (tmp_path / "echo.md").write_text("Read this: {{ text }}\nItems: {{items}}\nKeep {\"literal\": braces}", "utf-8")
    monkeypatch.setattr(llm, "PROMPTS_DIR", tmp_path)
    return tmp_path


VARS = {"text": "লগইন পেজে OTP লাগবে", "items": [{"id": "m1.s1", "text": "kemon acho"}]}


# ---------- prompts ----------


def test_render_prompt_fills_placeholders_and_appends_schema(prompts_dir):
    prompt = llm.render_prompt("echo", VARS, Answer)

    assert "Read this: লগইন পেজে OTP লাগবে" in prompt
    assert '"text": "kemon acho"' in prompt  # JSON, Bangla/Banglish not escaped
    assert 'Keep {"literal": braces}' in prompt
    assert "JSON object only" in prompt
    assert '"score"' in prompt  # the schema


def test_missing_variable_is_an_error(prompts_dir):
    with pytest.raises(KeyError, match="items"):
        llm.render_prompt("echo", {"text": "x"}, Answer)


def test_missing_prompt_file_is_an_error(prompts_dir):
    with pytest.raises(FileNotFoundError, match="nope.md"):
        llm.load_prompt("nope")


# ---------- complete_json ----------


def test_valid_answer(prompts_dir):
    provider = ScriptedProvider('{"answer": "ok", "score": 0.5}')

    result = LLMClient(provider).complete_json("echo", VARS, Answer)

    assert result == Answer(answer="ok", score=0.5)
    assert len(provider.prompts) == 1


def test_fenced_answer_is_accepted(prompts_dir):
    provider = ScriptedProvider('```json\n{"answer": "ok", "score": 1}\n```')
    assert LLMClient(provider).complete_json("echo", VARS, Answer).score == 1


@pytest.mark.parametrize("bad", ["not json at all", '{"answer": "ok"}', '{"answer": "ok", "score": "high"}'])
def test_invalid_answer_is_retried_once(prompts_dir, bad):
    provider = ScriptedProvider(bad, '{"answer": "second", "score": 0.1}')

    result = LLMClient(provider).complete_json("echo", VARS, Answer)

    assert result.answer == "second"
    assert len(provider.prompts) == 2
    assert "previous answer was rejected" in provider.prompts[1]
    assert provider.prompts[1].startswith(provider.prompts[0])


def test_invalid_twice_raises_clear_error(prompts_dir):
    provider = ScriptedProvider("nope", "[1, 2]")

    with pytest.raises(LLMError, match="invalid answer twice"):
        LLMClient(provider).complete_json("echo", VARS, Answer)
    assert len(provider.prompts) == 2


def test_call_is_logged_without_texts(prompts_dir, caplog):
    provider = ScriptedProvider("garbage লগইন", '{"answer": "ok", "score": 0.5}')

    with caplog.at_level(logging.INFO, logger="app.ai.llm"):
        LLMClient(provider).complete_json("echo", VARS, Answer)

    log = caplog.text
    assert "prompt=echo" in log and "input_tokens=10" in log and "output_tokens=5" in log
    assert "elapsed_ms=" in log and "attempt=2" in log
    assert "লগইন" not in log  # neither the prompt nor the answer text


def test_thinking_and_deadline_reach_the_provider(prompts_dir):
    provider = ScriptedProvider('{"answer": "ok", "score": 0.5}')

    LLMClient(provider).complete_json("echo", VARS, Answer, thinking="low", deadline=123.0)

    assert provider.options == [{"thinking": "low", "deadline": 123.0}]


def test_no_second_json_attempt_without_time_left(prompts_dir, monkeypatch):
    monkeypatch.setattr(llm, "clock", lambda: 100.0)
    provider = ScriptedProvider("not json", '{"answer": "ok", "score": 0.5}')

    with pytest.raises(LLMError, match="no time left"):
        LLMClient(provider).complete_json("echo", VARS, Answer, deadline=103.0)
    assert len(provider.prompts) == 1


def test_failed_call_is_logged_with_elapsed_time(prompts_dir, caplog):
    class Failing(ScriptedProvider):
        def generate(self, prompt, **options):
            raise LLMError("Gemini did not answer within 120 seconds. Try again later.")

    with caplog.at_level(logging.INFO, logger="app.ai.llm"), pytest.raises(LLMError):
        LLMClient(Failing()).complete_json("echo", VARS, Answer)
    assert "llm call failed prompt=echo" in caplog.text and "elapsed_ms=" in caplog.text


@pytest.mark.parametrize("value, level", [("low", "low"), (" LOW ", "low"), ("default", None), ("", None), (None, None)])
def test_thinking_level(value, level):
    assert llm.thinking_level(value) == level


# ---------- configuration ----------


def test_not_configured_names_variables_only(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_api_key", None)

    with pytest.raises(LLMNotConfiguredError) as exc:
        llm.get_llm()
    assert "LLM_MODEL" in str(exc.value) and "LLM_API_KEY" in str(exc.value)
    assert "LLM_PROVIDER" not in str(exc.value)


def test_unsupported_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "parrot")
    monkeypatch.setattr(settings, "llm_model", "m")
    monkeypatch.setattr(settings, "llm_api_key", FAKE_KEY)

    with pytest.raises(LLMNotConfiguredError, match="not supported"):
        llm.get_llm()


def test_gemini_is_built_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", " Gemini ")
    monkeypatch.setattr(settings, "llm_model", "gemini-2.5-flash")
    monkeypatch.setattr(settings, "llm_api_key", FAKE_KEY)

    monkeypatch.setattr(settings, "llm_timeout_seconds", 90.0)

    client = llm.get_llm()
    assert isinstance(client.provider, GeminiProvider)
    assert client.provider.model == "gemini-2.5-flash"
    assert client.provider.timeout == 90.0


def test_timeout_default_is_120_seconds():
    fields = type(settings).model_fields
    assert fields["llm_timeout_seconds"].default == 120.0
    assert fields["llm_extraction_thinking"].default == "low"


# ---------- Gemini over HTTP ----------


@respx.mock
def test_gemini_request_and_response():
    route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=gemini_fixture("gemini_response.json")))

    response = GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("প্রশ্ন: hello")

    request = route.calls.last.request
    assert request.headers["x-goog-api-key"] == FAKE_KEY
    assert FAKE_KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["contents"][0]["parts"][0]["text"] == "প্রশ্ন: hello"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "thinkingConfig" not in body["generationConfig"]  # no level given: the model decides
    assert request.extensions["timeout"]["read"] == 120.0
    assert json.loads(response.text) == {"answer": "লগইন পেজে OTP লাগবে", "score": 0.9}
    assert (response.input_tokens, response.output_tokens) == (120, 58)  # output includes thinking tokens


@respx.mock
def test_gemini_thinking_level_and_timeout_are_sent():
    route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=gemini_fixture("gemini_response.json")))

    GeminiProvider("gemini-2.5-flash", FAKE_KEY, timeout=45.0).generate("x", thinking="low")

    request = route.calls.last.request
    assert json.loads(request.content)["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert request.extensions["timeout"]["read"] == 45.0


@respx.mock
def test_gemini_attempt_timeout_is_cut_to_the_deadline(monkeypatch):
    monkeypatch.setattr(llm, "clock", lambda: 1000.0)
    route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=gemini_fixture("gemini_response.json")))

    GeminiProvider("gemini-2.5-flash", FAKE_KEY, timeout=120.0).generate("x", deadline=1030.0)

    assert route.calls.last.request.extensions["timeout"]["read"] == 30.0


@respx.mock
def test_gemini_no_attempt_after_the_deadline(monkeypatch):
    monkeypatch.setattr(llm, "clock", lambda: 1000.0)
    route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json=gemini_fixture("gemini_response.json")))

    with pytest.raises(LLMError, match="did not answer in time"):
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x", deadline=1002.0)
    assert route.call_count == 0


@respx.mock
def test_gemini_timeout_says_how_long_and_logs_elapsed(caplog):
    respx.post(GEMINI_URL).mock(side_effect=httpx.ReadTimeout("slow"))

    with caplog.at_level(logging.INFO, logger="app.ai.llm"), pytest.raises(LLMError) as exc:
        GeminiProvider("gemini-2.5-flash", FAKE_KEY, timeout=120.0).generate("x")

    assert "did not answer within 120 seconds" in str(exc.value)
    assert "status=timeout" in caplog.text and "elapsed_ms=" in caplog.text


@respx.mock
def test_gemini_error_is_clear_and_hides_key(caplog):
    respx.post(GEMINI_URL).mock(return_value=httpx.Response(400, json=gemini_fixture("gemini_error.json")))

    with caplog.at_level(logging.DEBUG), pytest.raises(LLMError) as exc:
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")

    assert "HTTP 400" in str(exc.value) and "API key not valid" in str(exc.value)
    assert FAKE_KEY not in str(exc.value)
    assert FAKE_KEY not in caplog.text


@respx.mock
def test_gemini_error_message_quoting_the_key_is_redacted():
    body = {"error": {"message": f"Key {FAKE_KEY} is disabled"}}
    respx.post(GEMINI_URL).mock(return_value=httpx.Response(403, json=body))

    with pytest.raises(LLMError) as exc:
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")
    assert FAKE_KEY not in str(exc.value) and "***" in str(exc.value)


# ---------- Gemini retries (429 / 503) ----------


@pytest.fixture
def waits(monkeypatch) -> list[float]:
    """Seconds the client would have slept before each retry."""
    calls: list[float] = []
    monkeypatch.setattr(llm, "sleep", calls.append)
    return calls


def ok():
    return httpx.Response(200, json=gemini_fixture("gemini_response.json"))


def overloaded(**headers):
    return httpx.Response(503, headers=headers, json={"error": {"code": 503, "message": "The model is overloaded."}})


@respx.mock
def test_gemini_503_then_200_is_retried(waits):
    route = respx.post(GEMINI_URL).mock(side_effect=[overloaded(), ok()])

    response = GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")

    assert response.input_tokens == 120
    assert route.call_count == 2
    assert waits == [2.0]


@respx.mock
def test_gemini_backoff_is_2_4_8_seconds(waits):
    route = respx.post(GEMINI_URL).mock(side_effect=[overloaded(), overloaded(), overloaded(), ok()])

    assert GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x").input_tokens == 120
    assert route.call_count == 4
    assert waits == [2.0, 4.0, 8.0]


@respx.mock
def test_gemini_gives_up_after_3_retries(waits, caplog):
    route = respx.post(GEMINI_URL).mock(side_effect=[overloaded() for _ in range(5)])

    with caplog.at_level(logging.WARNING, logger="app.ai.llm"), pytest.raises(LLMError) as exc:
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")

    assert route.call_count == 4  # first try + 3 retries
    assert waits == [2.0, 4.0, 8.0]
    assert "overloaded (HTTP 503)" in str(exc.value) and "Try again in a few minutes" in str(exc.value)
    assert "retry=3" in caplog.text and FAKE_KEY not in caplog.text


@respx.mock
def test_gemini_429_respects_retry_after(waits):
    limited = httpx.Response(429, headers={"Retry-After": "5"}, json={"error": {"message": "Quota exceeded."}})
    respx.post(GEMINI_URL).mock(side_effect=[limited, overloaded(**{"Retry-After": "0.5"}), ok()])

    assert GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x").input_tokens == 120
    assert waits == [5.0, 0.5]


@respx.mock
def test_gemini_no_retry_when_the_wait_would_pass_the_deadline(waits, monkeypatch):
    monkeypatch.setattr(llm, "clock", lambda: 1000.0)
    route = respx.post(GEMINI_URL).mock(side_effect=[overloaded(), ok()])

    # 2 s backoff + 5 s minimum attempt would end after the deadline
    with pytest.raises(LLMError, match=r"overloaded \(HTTP 503\)"):
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x", deadline=1006.0)
    assert route.call_count == 1
    assert waits == []


@respx.mock
def test_gemini_each_attempt_is_logged(waits, caplog):
    respx.post(GEMINI_URL).mock(side_effect=[overloaded(), ok()])

    with caplog.at_level(logging.INFO, logger="app.ai.llm"):
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")

    assert "attempt=1 status=503 elapsed_ms=" in caplog.text
    assert "attempt=2 status=200 elapsed_ms=" in caplog.text


@respx.mock
def test_gemini_unusable_retry_after_falls_back_to_backoff(waits):
    respx.post(GEMINI_URL).mock(side_effect=[overloaded(**{"Retry-After": "soon"}), overloaded(), ok()])

    GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")

    assert waits == [2.0, 4.0]


@respx.mock
def test_gemini_daily_quota_fails_at_once(waits, caplog):
    body = gemini_fixture("gemini_daily_quota.json")  # no Retry-After header; RetryInfo.retryDelay "44953s"
    route = respx.post(GEMINI_URL).mock(side_effect=[httpx.Response(429, json=body), ok()])

    with caplog.at_level(logging.INFO, logger="app.ai.llm"), pytest.raises(LLMError) as exc:
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")

    assert str(exc.value) == "Gemini daily free quota used up. Try again in about 12 hours."
    assert route.call_count == 1
    assert waits == []
    assert "not retrying" in caplog.text and FAKE_KEY not in caplog.text


def _quota_429(message: str, retry_delay: str | None = None, quota_id: str | None = None, **headers):
    details = []
    if quota_id:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]})
    if retry_delay:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    body = {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED", "details": details}}
    return httpx.Response(429, headers=headers, json=body)


@respx.mock
def test_gemini_retry_delay_over_60s_in_body_fails_at_once(waits):
    route = respx.post(GEMINI_URL).mock(side_effect=[_quota_429("Quota exceeded.", "7200s"), ok()])

    with pytest.raises(LLMError, match=r"^Gemini daily free quota used up\. Try again in about 2 hours\.$"):
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")
    assert (route.call_count, waits) == (1, [])


@respx.mock
def test_gemini_too_long_retry_after_header_fails_at_once(waits):
    route = respx.post(GEMINI_URL).mock(side_effect=[_quota_429("Quota exceeded.", **{"Retry-After": "3600"}), ok()])

    with pytest.raises(LLMError, match=r"about 1 hour\.$"):
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")
    assert (route.call_count, waits) == (1, [])


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        _quota_429("Quota exceeded for quota metric 'Generate requests' and limit 'requests per day'."),
        _quota_429("Quota exceeded.", quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier"),
    ],
)
def test_gemini_per_day_quota_without_delay_fails_at_once(waits, response):
    route = respx.post(GEMINI_URL).mock(side_effect=[response, ok()])

    with pytest.raises(LLMError, match=r"^Gemini daily free quota used up\. Try again tomorrow\.$"):
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")
    assert (route.call_count, waits) == (1, [])


@respx.mock
def test_gemini_short_retry_delay_in_body_is_waited_for(waits):
    per_minute = _quota_429("Quota exceeded per minute.", "17s", quota_id="GenerateRequestsPerMinutePerProjectPerModel")
    respx.post(GEMINI_URL).mock(side_effect=[per_minute, ok()])

    assert GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x").input_tokens == 120
    assert waits == [17.0]


@pytest.mark.parametrize(
    "seconds, text",
    [(None, "Try again tomorrow."), (61, "about 1 hour."), (5400, "about 2 hours."), (44953, "about 12 hours.")],
)
def test_daily_quota_message(seconds, text):
    assert llm.daily_quota_message(seconds).endswith(text)


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_gemini_other_errors_are_not_retried(waits, status):
    route = respx.post(GEMINI_URL).mock(side_effect=[httpx.Response(status, json={"error": {"message": "No."}}), ok()])

    with pytest.raises(LLMError, match=f"HTTP {status}"):
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")
    assert route.call_count == 1
    assert waits == []


@respx.mock
def test_gemini_non_json_error_body(waits):
    respx.post(GEMINI_URL).mock(side_effect=[httpx.Response(503, text="<html>Service Unavailable</html>")] * 4)

    with pytest.raises(LLMError, match=r"overloaded \(HTTP 503\)\. Try again"):
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")


@respx.mock
def test_gemini_blocked_prompt():
    respx.post(GEMINI_URL).mock(return_value=httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))

    with pytest.raises(LLMError, match="no answer.*SAFETY"):
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")


@respx.mock
def test_gemini_network_error():
    respx.post(GEMINI_URL).mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(LLMError, match="Could not reach Gemini"):
        GeminiProvider("gemini-2.5-flash", FAKE_KEY).generate("x")
