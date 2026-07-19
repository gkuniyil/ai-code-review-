# backend/embeddings.py
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# initialize the Local Transformer Engine
model = SentenceTransformer("all-MiniLM-L6-v2")

# iinitialize the Volatile Math Index
# IndexFlatL2 executes a brute force exact search by measuring Euclidean Distance (L2)  between the incoming query vector and all recorded vectors in memory.
index = faiss.IndexFlatL2(384)

# Serves as the index bridge: array position X maps directly to index position X in FAISS.
review_store = []


def generate_embedding(text: str) -> np.ndarray:
    """
    Transforms structural text strings into a normalized 384-dimensional vector array.
    """
    # model.encode expects an iterable list, we extract index 0 to pull the raw array
    return model.encode([text])[0]


def index_review(review_id: int, comment: str, suggested_fix: str):
    """
    Appends a new review vector to the active FAISS index frame.
    Invoked immediately following database synchronization.
    """
    # Create an integrated context string so fixes and bugs are bound conceptually
    text = f"{comment} {suggested_fix or ''}"
    embedding = generate_embedding(text)
    
    # FAISS expects a 2D float32 matrix row array: shape (1, 384)
    vector_matrix = np.array([embedding]).astype('float32')
    index.add(vector_matrix)
    
    # Append corresponding contextual metadata at the exact same position index
    review_store.append({
        "review_id": review_id,
        "comment": comment,
        "suggested_fix": suggested_fix
    })


def search_similar(code_snippet: str, k: int = 3) -> list:
    """
    Scans the live coordinate grid to find the 'k' nearest vector neighbors.
    """
    # Guard routine to handle blank states before any reviews have occurred
    if index.ntotal == 0:
        return []

    # Vectorize incoming query text
    query_vector = np.array([generate_embedding(code_snippet)]).astype('float32')

    # Query the memory frame
    # distances: proximity variations (lower float values mean closer conceptual match)
    # indices: array of matrix row position markers that matched the geometry
    distances, indices = index.search(query_vector, k)

    results = []
    for i, idx in enumerate(indices[0]):
        # FAISS returns -1 for indices if it doesn't find enough points to satisfy k
        if idx != -1 and idx < len(review_store):
            results.append({
                "review": review_store[idx],
                "distance": float(distances[0][i])
            })
    return results