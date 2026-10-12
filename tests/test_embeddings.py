from unittest.mock import patch

import numpy as np

from backend import embeddings
from backend.embeddings import EMBEDDING_DIM, find_similar, from_bytes, to_bytes

# fake embedding model: each "topic" word gets its own direction, so texts about the
# same topic have cosine similarity 1 and different topics 0 — no model download needed
TOPICS = ["select", "loop", "secret", "css"]


def fake_embed(texts):
    vectors = np.zeros((len(texts), EMBEDDING_DIM), dtype="float32")
    for row, text in enumerate(texts):
        for col, topic in enumerate(TOPICS):
            if topic in text.lower():
                vectors[row, col] = 1.0
        norm = np.linalg.norm(vectors[row])
        if norm:
            vectors[row] /= norm
    return vectors


def _past(text, comment, agent_type="security"):
    return {
        "embedding": fake_embed([text])[0],
        "agent_type": agent_type,
        "severity": "high",
        "file_path": "app.py",
        "comment": comment,
    }


@patch.object(embeddings, "embed", side_effect=fake_embed)
def test_find_similar_returns_matching_past_findings_ranked(_):
    past = [
        _past("SELECT * FROM users WHERE id = " + "x", "SQL injection"),
        _past("for row in rows: loop query", "N+1 query", agent_type="optimization"),
        _past("API_SECRET = 'abc'", "hardcoded secret"),
    ]

    results = find_similar(["q = 'SELECT name FROM t' + user"], past)

    assert [r["comment"] for r in results] == ["SQL injection"]
    assert results[0]["similarity"] == 1.0
    assert type(results[0]["similarity"]) is float   # not np.float32 — results must be JSON-serializable
    assert "embedding" not in results[0]       # raw vectors aren't passed on to prompts


@patch.object(embeddings, "embed", side_effect=fake_embed)
def test_find_similar_ignores_matches_below_threshold(_):
    past = [_past("SELECT * FROM users", "SQL injection")]

    assert find_similar([".btn { css: red }"], past) == []


@patch.object(embeddings, "embed", side_effect=fake_embed)
def test_find_similar_keeps_each_past_finding_once_with_best_score(_):
    past = [_past("SELECT * FROM users", "SQL injection")]

    # two chunks both match the same past finding
    results = find_similar(["SELECT a", "SELECT b"], past)

    assert len(results) == 1


@patch.object(embeddings, "embed", side_effect=fake_embed)
def test_find_similar_caps_results_at_k(_):
    past = [_past(f"SELECT {i}", f"finding {i}") for i in range(10)]

    assert len(find_similar(["SELECT x"], past, k=3)) == 3


def test_find_similar_with_no_history_returns_nothing_without_loading_model():
    with patch.object(embeddings, "embed") as mock_embed:
        assert find_similar(["SELECT x"], []) == []
    mock_embed.assert_not_called()


def test_embedding_bytes_round_trip():
    vector = fake_embed(["select loop"])[0]

    assert np.array_equal(from_bytes(to_bytes(vector)), vector)
    assert len(to_bytes(vector)) == EMBEDDING_DIM * 4    # 384 float32s
