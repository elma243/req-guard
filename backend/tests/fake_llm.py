"""A stand-in for LLMClient: answers "extract_requirements" from a fixture table, "classify_pairs" from
`relations`, and the clarification, test case, group name and trace link prompts from settable answers; never
calls an API."""

import json
from pathlib import Path

from app.ai.llm import LLMClient, LLMError

ANSWERS = Path(__file__).parent / "fixtures" / "llm" / "extract_answers.json"


class FakeLLM(LLMClient):
    """For every sentence that contains a `match` text of the table, returns that entry's requirements.

    `fail_on_call=n` raises LLMError on the n-th extraction call (1-based); `extra` items are added to every
    answer. Each answer is reported as given by `model` (`last_model`).

    Pairs: `relate(older, newer, relation)` sets the answer for two statements; other pairs get
    `default_relation`. `pairs_fail=True` raises LLMError instead. Pair calls are in `pair_calls`, not `calls`.
    """

    def __init__(self, fail_on_call: int | None = None, extra: list[dict] | None = None, model: str = "fake-model"):
        self.table = json.loads(ANSWERS.read_text(encoding="utf-8"))
        self.fail_on_call = fail_on_call
        self.extra = extra or []
        self.model = model
        self.calls: list[dict] = []
        self.options: list[dict] = []  # thinking / deadline of each call
        self.relations: dict[tuple[str, str], str] = {}
        self.default_relation = "not_enough_information"
        self.pairs_fail = False
        self.pair_calls: list[list[dict]] = []
        # Clarification questions: requirement id -> its answer ({"questions": [...], "not_enough_information"}).
        # Requirements not in the table get one question per finding.
        self.question_answers: dict[int, dict] = {}
        self.question_calls: list[list[dict]] = []
        self.questions_fail: Exception | None = None
        # Proposals: the answer to "clarification_proposal"
        self.proposal: dict = {"statement": "", "not_enough_information": True, "reason": "Fake: nothing to add."}
        self.proposal_calls: list[dict] = []
        self.proposal_fail: Exception | None = None
        # Test cases: statement -> answer. Other statements get `default_test_cases(run)`: 2 criteria and 3 test
        # cases (positive, negative, edge) whose titles name the run, so a regeneration writes new titles.
        self.test_case_answers: dict[str, dict] = {}
        self.test_case_calls: list[dict] = []
        # Raised on every call, or only on the n-th call (1-based)
        self.test_cases_fail: Exception | None = None
        self.test_cases_fail_on: int | None = None
        # Group names (Step 9): ref ("G1"...) -> {"name", "reason"}; other refs get "Fake Group <n>"
        self.group_names: dict[str, dict] = {}
        self.group_name_calls: list[list[dict]] = []
        self.group_names_fail: Exception | None = None
        # Trace links (Step 10): (requirement statement, item name) -> {"verdict", "confidence", "reason"}; other
        # pairs get `default_link_verdict`
        self.link_verdicts: dict[tuple[str, str], dict] = {}
        self.default_link_verdict: dict = {"verdict": "link", "confidence": 0.8, "reason": "Fake: the names match."}
        self.verify_link_calls: list[list[dict]] = []
        self.verify_links_fail: Exception | None = None

    def relate(self, older: str, newer: str, relation: str) -> None:
        self.relations[(older, newer)] = relation

    def _classify_pairs(self, variables, schema):
        self.pair_calls.append(variables["pairs"])
        if self.pairs_fail:
            raise LLMError("All AI models are busy right now. Try again in a few minutes.")
        self.last_model = self.model
        answers = []
        for pair in variables["pairs"]:
            relation = self.relations.get((pair["older"], pair["newer"]), self.default_relation)
            answers.append({"pair_id": pair["pair_id"], "relation": relation, "reason": f"Fake reason: {relation}."})
        return schema.model_validate({"pairs": answers})

    def _clarification_questions(self, variables, schema):
        self.question_calls.append(variables["requirements"])
        if self.questions_fail:
            raise self.questions_fail
        self.last_model = self.model
        answers = []
        for item in variables["requirements"]:
            default = {
                "questions": [
                    {"finding_id": f["finding_id"], "text": f'Could you tell us more about "{f["evidence"]}"?'}
                    for f in item["findings"]
                ]
            }
            answers.append({"requirement_id": item["requirement_id"], **self.question_answers.get(item["requirement_id"], default)})
        return schema.model_validate({"requirements": answers})

    def _clarification_proposal(self, variables, schema):
        self.proposal_calls.append(variables)
        if self.proposal_fail:
            raise self.proposal_fail
        self.last_model = self.model
        return schema.model_validate(self.proposal)

    def _test_cases(self, variables, schema):
        self.test_case_calls.append(variables)
        run = len(self.test_case_calls)
        if self.test_cases_fail and (self.test_cases_fail_on in (None, run)):
            raise self.test_cases_fail
        self.last_model = self.model
        statement = variables["requirement"]["statement"]
        return schema.model_validate(self.test_case_answers.get(statement, default_test_cases(run)))

    def _group_names(self, variables, schema):
        self.group_name_calls.append(variables["clusters"])
        if self.group_names_fail:
            raise self.group_names_fail
        self.last_model = self.model
        groups = [
            {"ref": c["ref"], **self.group_names.get(c["ref"], {"name": f"Fake Group {i + 1}", "reason": "Fake reason."})}
            for i, c in enumerate(variables["clusters"])
        ]
        return schema.model_validate({"groups": groups})

    def _verify_links(self, variables, schema):
        self.verify_link_calls.append(variables["pairs"])
        if self.verify_links_fail:
            raise self.verify_links_fail
        self.last_model = self.model
        links = [
            {
                "pair_id": p["pair_id"],
                **self.link_verdicts.get((p["requirement"]["statement"], p["item"]["name"]), self.default_link_verdict),
            }
            for p in variables["pairs"]
        ]
        return schema.model_validate({"links": links})

    @property
    def verified_pairs(self) -> list[tuple[str, str]]:
        """(requirement code, item name) of every pair sent to "verify_links"."""
        return [(p["requirement"]["code"], p["item"]["name"]) for call in self.verify_link_calls for p in call]

    def complete_json(self, prompt_name, variables, schema, **options):
        if prompt_name == "verify_links":
            return self._verify_links(variables, schema)
        if prompt_name == "group_names":
            return self._group_names(variables, schema)
        if prompt_name == "classify_pairs":
            return self._classify_pairs(variables, schema)
        if prompt_name == "clarification_questions":
            return self._clarification_questions(variables, schema)
        if prompt_name == "clarification_proposal":
            return self._clarification_proposal(variables, schema)
        if prompt_name == "test_cases":
            return self._test_cases(variables, schema)
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


def default_test_cases(run: int = 1) -> dict:
    return {
        "criteria": [
            {"ref": "C1", "text": f"A valid request gives the expected result (run {run})."},
            {"ref": "C2", "text": f"Input at the limit is handled (run {run})."},
        ],
        "test_cases": [
            {
                "title": f"Valid request works (run {run})",
                "type": "positive",
                "given": "a signed-in admin",
                "when": "they make a valid request",
                "then": "the expected result is shown",
                "criterion_ref": "C1",
            },
            {
                "title": f"Invalid request is refused (run {run})",
                "type": "negative",
                "given": "a signed-in admin",
                "when": "they make an invalid request",
                "then": "a clear error is shown",
                "criterion_ref": "C1",
            },
            {
                "title": f"Request at the limit (run {run})",
                "type": "edge",
                "given": "a signed-in admin",
                "when": "they make a request exactly at the limit",
                "then": "it is handled without an error",
                "criterion_ref": "C2",
            },
        ],
    }
