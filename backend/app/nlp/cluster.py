"""Clustering of requirement embeddings and rule-based group names (Step 9, FR-11).

Pure functions with no database: app/tasks/grouping.py gives them the vectors and statements.
"""

import re
from collections import Counter
from collections.abc import Sequence

import numpy as np

from app.nlp.lexicon import STOP_WORDS

# Cosine distance (1 - similarity) below which requirements end up in one cluster (average linkage)
DISTANCE_THRESHOLD = 0.5
NAME_WORDS = 3

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Requirement wording that says nothing about the topic
_EXTRA_STOP = {"admin", "admins", "able", "also", "both", "other", "within", "about", "which", "what", "them"}


def cluster(vectors: Sequence[np.ndarray], threshold: float = DISTANCE_THRESHOLD) -> list[int]:
    """A cluster label per vector (labels start at 0). Vectors have length 1.

    AgglomerativeClustering with cosine distance and average linkage, cut at `threshold`; one vector alone is its
    own cluster.
    """
    if len(vectors) == 0:
        return []
    if len(vectors) == 1:
        return [0]
    from sklearn.cluster import AgglomerativeClustering

    model = AgglomerativeClustering(
        n_clusters=None, metric="cosine", linkage="average", distance_threshold=threshold
    )
    labels = model.fit_predict(np.vstack(vectors))
    # Number the clusters in order of first appearance, so the result does not depend on sklearn's numbering
    order: dict[int, int] = {}
    return [order.setdefault(int(label), len(order)) for label in labels]


def topic_words(text: str) -> list[str]:
    """The words of a statement that can say what it is about, lower case, in order."""
    words = [w.lower() for w in _WORD.findall(text)]
    return [w for w in words if len(w) >= 3 and w not in STOP_WORDS and w not in _EXTRA_STOP]


def rule_name(statements: Sequence[str], words: int = NAME_WORDS) -> str:
    """A group name from the most frequent topic words, e.g. "Order, Export, Csv"; the earliest word wins a tie."""
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    for statement in statements:
        for word in topic_words(statement):
            counts[word] += 1
            first_seen.setdefault(word, len(first_seen))
    best = sorted(counts, key=lambda w: (-counts[w], first_seen[w]))[:words]
    return ", ".join(w.capitalize() for w in best) or "Other requirements"
