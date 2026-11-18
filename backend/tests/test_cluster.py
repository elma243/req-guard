"""Pure clustering helpers: embedding clusters and rule-based group names."""

import numpy as np

from app.nlp.cluster import cluster, rule_name, topic_words
from tests.fake_models import unit, vector_at


def _v(vector) -> np.ndarray:
    return np.asarray(vector, dtype=np.float32)


def test_similar_vectors_share_a_cluster_and_a_far_one_stays_apart():
    labels = cluster([_v(unit(0)), _v(unit(7)), _v(vector_at(0.9, axis=1))])
    assert labels[0] == labels[2]
    assert labels[1] != labels[0]
    assert labels == [0, 1, 0]  # numbered in order of first appearance


def test_threshold_decides_how_close_is_close_enough():
    vectors = [_v(unit(0)), _v(vector_at(0.4, axis=1))]  # similarity 0.4: distance 0.6
    assert cluster(vectors) == [0, 1]
    assert cluster(vectors, threshold=0.7) == [0, 0]


def test_no_vector_and_one_vector():
    assert cluster([]) == []
    assert cluster([_v(unit(3))]) == [0]


def test_rule_name_uses_the_most_frequent_topic_words():
    name = rule_name(
        [
            "The admin shall export the sales report as PDF.",
            "The admin shall export the stock report as PDF.",
        ]
    )
    assert name == "Export, Report, Pdf"


def test_rule_name_without_topic_words():
    assert rule_name(["The system shall be able to."]) == "Other requirements"


def test_topic_words_drop_requirement_wording():
    assert topic_words("The admin shall export the order list as CSV.") == ["export", "order", "list", "csv"]
