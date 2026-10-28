"""A stand-in for LLMClient: answers "extract_requirements" from a fixture table, never calls an API."""

import json
from pathlib import Path

from app.ai.llm import LLMClient, LLMError

ANSWERS = Path(__file__).parent / "fixtures" / "llm" / "extract_answers.json"


class FakeLLM(LLMClient):
    """For every sentence that contains a `match` text of the table, returns that entry's requirements.

    `fail_on_call=n` raises LLMError on the n-th call (1-based); `extra` items are added to every answer.
    Each answer is reported as given by `model` (`last_model`).
    """

    def __init__(self, fail_on_call: int | None = None, extra: list[dict] | None = None, model: str = "fake-model"):
        self.table = json.loads(ANSWERS.read_text(encoding="utf-8"))
        self.fail_on_call = fail_on_call
        self.extra = extra or []
        self.model = model
        self.calls: list[dict] = []
        self.options: list[dict] = []  # thinking / deadline of each call

    def complete_json(self, prompt_name, variables, schema, **options):
        assert prompt_name == "extract_requirements"
        self.calls.append(variables)
        self.options.append(options)
        if self.fail_on_call == len(self.calls):
            raise LLMError("The AI returned an invalid answer twice. Try again.")
        found = []
        for message in variables["batch"]:
            for sentence in message["sentences"]:
                for entry in self.table:
                    if entry["match"] in sentence["text"]:
                        found += [{**r, "sentence_ids": [sentence["id"]]} for r in entry["requirements"]]
        self.last_model = self.model
        return schema.model_validate({"requirements": found + self.extra})

    @property
    def sentences_sent(self) -> list[str]:
        return [s["text"] for call in self.calls for m in call["batch"] for s in m["sentences"]]
