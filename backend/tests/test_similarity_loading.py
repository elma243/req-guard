"""Loading the similarity models lazily, and the helpers around them. No real model is loaded here."""

import numpy as np
import pytest

from app.core.config import settings
from app.nlp import similarity
from app.nlp.nli import contradiction_both_ways
from app.nlp.similarity import LazyModel, ModelUnavailable, similarity_matrix
from tests.fake_models import FakeNLI


def test_a_model_is_loaded_once():
    loads = []
    lazy = LazyModel("test model", lambda: loads.append(1) or object())
    first = lazy.get()
    assert lazy.get() is first
    assert loads == [1]


def test_switched_off_models_are_not_loaded(monkeypatch):
    monkeypatch.setattr(settings, "similarity_enabled", False)
    lazy = LazyModel("test model", lambda: pytest.fail("must not load"))
    with pytest.raises(ModelUnavailable, match="SIMILARITY_ENABLED=false"):
        lazy.get()


def test_a_failed_load_is_remembered_then_tried_again(monkeypatch):
    attempts = []

    def load():
        attempts.append(1)
        raise OSError("no network")

    clock = [1000.0]
    monkeypatch.setattr(similarity.time, "monotonic", lambda: clock[0])
    lazy = LazyModel("test model", load)
    with pytest.raises(ModelUnavailable, match="The test model could not be loaded: no network"):
        lazy.get()
    with pytest.raises(ModelUnavailable, match="no network"):
        lazy.get()
    assert len(attempts) == 1  # the second request did not wait for another try

    clock[0] += similarity.RETRY_AFTER_SECONDS
    with pytest.raises(ModelUnavailable):
        lazy.get()
    assert len(attempts) == 2


def test_contradiction_counts_the_higher_direction_in_one_call():
    model = FakeNLI()
    model.contradict("B", "A", 0.8)
    model.contradict("C", "D", 0.3)
    assert contradiction_both_ways(model, [("A", "B"), ("C", "D")]) == [0.8, 0.3]
    assert model.calls == [[("A", "B"), ("C", "D"), ("B", "A"), ("D", "C")]]


def test_contradiction_of_no_pairs_asks_nothing():
    model = FakeNLI()
    assert contradiction_both_ways(model, []) == []
    assert model.calls == []


def test_similarity_matrix_is_the_cosine_of_unit_vectors():
    a, b = np.array([1.0, 0.0]), np.array([0.6, 0.8])
    assert np.allclose(similarity_matrix([a, b]), [[1.0, 0.6], [0.6, 1.0]])
    assert similarity_matrix([]).shape == (0, 0)
