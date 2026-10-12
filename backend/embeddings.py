# backend/embeddings.py
"""
review memory — retrieval over past findings (RAG).

each saved finding stores the code it pointed at plus an embedding of that code.
before a new review, chunks of the new diff are embedded and searched against
past findings from the same repo with FAISS; close matches are handed to the
agents as hints ("similar code here was flagged for SQL injection before").

postgres is the source of truth for embeddings — the FAISS index is rebuilt
per review from those rows, so nothing is lost on restart and every worker
process sees the same memory.
"""
import faiss
import numpy as np

EMBEDDING_DIM = 384      # output size of all-MiniLM-L6-v2
MIN_SIMILARITY = 0.35    # cosine similarity cutoff — measured: related code ~0.4-0.6, unrelated < 0.2
MEMORY_SEARCH_LIMIT = 2000  # most recent past findings searched per repo — bounds memory and search time

_model = None


def _get_model():
    """
    loads the Hugging Face sentence-transformer once per process, on first use.
    importing lazily keeps torch out of processes that never embed (the web server, tests).
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """
    turns texts into a (len(texts), 384) float32 matrix of unit-length vectors.
    unit length means inner product == cosine similarity, which IndexFlatIP computes.
    """
    vectors = _get_model().encode(texts, normalize_embeddings=True)
    return np.asarray(vectors, dtype="float32")


def to_bytes(vector: np.ndarray) -> bytes:
    """serializes one embedding for the reviews.embedding column"""
    return np.asarray(vector, dtype="float32").tobytes()


def from_bytes(data: bytes) -> np.ndarray:
    """inverse of to_bytes"""
    return np.frombuffer(data, dtype="float32")


def find_similar(query_texts: list[str], past: list[dict], k: int = 5) -> list[dict]:
    """
    returns up to k past findings whose code is most similar to any query text.

    past: dicts with an "embedding" (np.ndarray) plus any metadata to return.
    each result is the past dict (minus "embedding") plus its best "similarity".
    """
    if not query_texts or not past:
        return []

    # IndexFlatIP = exact (brute-force) inner-product search. fine for thousands of
    # vectors; millions would need an approximate index (IVF/HNSW) or pgvector
    index = faiss.IndexFlatIP(EMBEDDING_DIM)
    index.add(np.stack([p["embedding"] for p in past]).astype("float32"))

    scores, ids = index.search(embed(query_texts), min(k, len(past)))

    # a past finding can match several chunks — keep its best score
    best: dict[int, float] = {}
    for row_scores, row_ids in zip(scores, ids):
        for score, idx in zip(row_scores, row_ids):
            if idx != -1 and score >= MIN_SIMILARITY:
                best[int(idx)] = max(best.get(int(idx), 0.0), float(score))  # plain python types, so results are JSON-safe

    ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)[:k]
    return [
        {**{key: v for key, v in past[idx].items() if key != "embedding"}, "similarity": round(score, 3)}
        for idx, score in ranked
    ]
