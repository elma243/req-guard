"""Stand-ins for the similarity models, so tests never import torch or download anything.

FakeEmbedder: a bag-of-words vector (crc32 of each word into 384 slots), so the same words give similarity 1.0
and unrelated sentences about 0. `vectors` sets an exact vector for a text; `vector_at(s)` gives one with
cosine `s` to the basis vector BASE.

FakeNLI: contradiction probability per (premise, hypothesis) from `scores`; `contradict(a, b, p)` sets one
direction only, so tests also show that the other direction is asked.
"""

import math
import re
import zlib
from collections.abc import Sequence

import numpy as np

DIMENSIONS = 384


def unit(index: int) -> list[float]:
    vector = [0.0] * DIMENSIONS
    vector[index] = 1.0
    return vector


BASE = unit(0)


def vector_at(similarity: float, axis: int = 1) -> list[float]:
    """A unit vector whose cosine similarity with BASE is `similarity` (`axis` keeps such vectors apart)."""
    vector = [0.0] * DIMENSIONS
    vector[0] = similarity
    vector[axis] = math.sqrt(max(0.0, 1.0 - similarity * similarity))
    return vector


class FakeEmbedder:
    def __init__(self, name: str = "fake-embedder") -> None:
        self.name = name
        self.vectors: dict[str, list[float]] = {}
        self.calls: list[list[str]] = []

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vectors.get(text) or self._bag_of_words(text) for text in texts]

    @staticmethod
    def _bag_of_words(text: str) -> list[float]:
        vector = np.zeros(DIMENSIONS)
        for word in re.findall(r"\w+", text.lower()):
            vector[zlib.crc32(word.encode()) % DIMENSIONS] += 1.0
        norm = np.linalg.norm(vector)
        return (vector / norm if norm else vector).tolist()

    @property
    def encoded(self) -> list[str]:
        return [text for call in self.calls for text in call]


class FakeNLI:
    def __init__(self, default: float = 0.02) -> None:
        self.default = default
        self.scores: dict[tuple[str, str], float] = {}
        self.calls: list[list[tuple[str, str]]] = []

    def contradict(self, premise: str, hypothesis: str, probability: float = 0.9) -> None:
        self.scores[(premise, hypothesis)] = probability

    def contradiction(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.calls.append(list(pairs))
        return [self.scores.get(pair, self.default) for pair in pairs]


class FakeModels:
    def __init__(self) -> None:
        self.embedder = FakeEmbedder()
        self.nli = FakeNLI()
        # Set to a reason to act as if the models could not load
        self.unavailable: str | None = None
