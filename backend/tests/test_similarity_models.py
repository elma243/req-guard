"""The real similarity models. Skipped unless RUN_MODEL_TESTS=1, because the first run downloads about 670 MB.

    cd backend && RUN_MODEL_TESTS=1 pytest tests/test_similarity_models.py

Running it once before a demo also puts the models in the Hugging Face cache, so the first Validate is fast.
"""

import os

import numpy as np
import pytest

from app.nlp import nli, similarity
from app.nlp.pair_checks import CONTRADICTION, DUPLICATE, PairItem, classify_pair

pytestmark = pytest.mark.skipif(os.environ.get("RUN_MODEL_TESTS") != "1", reason="set RUN_MODEL_TESTS=1 to run")

PASSWORD = "Users shall be able to reset their password by email."
PASSWORD_AGAIN = "Users must be able to reset their password via email."
DARK_MODE = "The app shall support dark mode."
NO_DARK_MODE = "The app shall not support dark mode."
REPORT = "The admin shall download a monthly sales report as PDF."


@pytest.fixture(scope="module")
def embedder():
    return similarity._embedder.get()  # the real loader; the autouse fixture only replaces get_embedder


@pytest.fixture(scope="module")
def nli_model():
    return nli._nli.get()


def cosine(embedder, a: str, b: str) -> float:
    first, second = embedder.encode([a, b])
    assert len(first) == 384
    return float(similarity.similarity_matrix([np.asarray(first), np.asarray(second)])[0, 1])


def test_paraphrase_is_a_possible_duplicate(embedder):
    assert cosine(embedder, PASSWORD, PASSWORD_AGAIN) >= DUPLICATE


def test_unrelated_requirements_are_far_apart(embedder):
    assert cosine(embedder, PASSWORD, REPORT) < 0.5


def test_opposite_statements_contradict(nli_model):
    [score] = nli.contradiction_both_ways(nli_model, [(DARK_MODE, NO_DARK_MODE)])
    assert score >= CONTRADICTION


def test_paraphrase_does_not_contradict(nli_model):
    [score] = nli.contradiction_both_ways(nli_model, [(PASSWORD, PASSWORD_AGAIN)])
    assert score < CONTRADICTION


def test_end_to_end_pair_gets_a_conflict(embedder, nli_model):
    s = cosine(embedder, DARK_MODE, NO_DARK_MODE)
    [c] = nli.contradiction_both_ways(nli_model, [(DARK_MODE, NO_DARK_MODE)])
    old = PairItem(id=1, code="REQ-001", number=1, status="suggested", statement=DARK_MODE)
    new = PairItem(id=2, code="REQ-002", number=2, status="suggested", statement=NO_DARK_MODE)
    assert classify_pair(old, new, s, c).category == "conflict"
