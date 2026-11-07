"""Natural language inference: how likely two requirement statements contradict each other.

cross-encoder/nli-deberta-v3-small by default, local on the CPU, loaded on first use (see app/nlp/similarity.py
for the loading rules). NLI is directional ("A, so B?"), so every pair is checked both ways in one batch and the
higher contradiction probability counts.
"""

from collections.abc import Sequence
from typing import Protocol

import numpy as np

from app.core.config import settings
from app.nlp.similarity import LazyModel, ModelUnavailable

__all__ = ["ContradictionModel", "ModelUnavailable", "contradiction_both_ways", "get_nli"]

BATCH_SIZE = 16


class ContradictionModel(Protocol):
    def contradiction(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """For each (premise, hypothesis): the probability that they contradict each other (0-1)."""
        ...


class CrossEncoderNLI:
    def __init__(self, name: str) -> None:
        import torch
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(name, device="cpu", activation_fn=torch.nn.Identity())
        labels = {int(i): str(label).lower() for i, label in self._model.config.id2label.items()}
        found = [i for i, label in labels.items() if label == "contradiction"]
        if not found:
            raise ValueError(f"{name} has no 'contradiction' label (labels: {labels})")
        self._index = found[0]

    def contradiction(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        logits = np.asarray(
            self._model.predict(list(pairs), batch_size=BATCH_SIZE, show_progress_bar=False), dtype=np.float64
        )
        logits = logits - logits.max(axis=1, keepdims=True)
        probabilities = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
        return probabilities[:, self._index].tolist()


_nli: LazyModel[ContradictionModel] = LazyModel("NLI model", lambda: CrossEncoderNLI(settings.nli_model))


def get_nli() -> ContradictionModel:
    """The NLI model; raises ModelUnavailable."""
    return _nli.get()


def contradiction_both_ways(model: ContradictionModel, pairs: Sequence[tuple[str, str]]) -> list[float]:
    """For each (a, b): the higher contradiction probability of a→b and b→a, from one batched call."""
    if not pairs:
        return []
    scores = model.contradiction([*pairs, *((b, a) for a, b in pairs)])
    n = len(pairs)
    return [max(scores[i], scores[n + i]) for i in range(n)]
