"""
mechanic_library.py
===================
DesignVoyager — Mechanic Library

Persistent memory for the system. Stores validated mechanics in a
JSON file (library.json) so they survive between runs.

Each mechanic is stored with:
  - mechanic_name, mechanic_type, description, justification, python_code
  - scores: playability, balance, depth, aggregate (from playtesting)
  - iteration: which loop iteration it was accepted on
  - embedding: OpenAI vector for semantic retrieval (text-embedding-3-small)

Retrieval
---------
retrieve(k, query=None)
  - With query   : semantic similarity (cosine) between the query and each
                   mechanic's embedding, blended with aggregate score.
                   A light diversity filter still avoids returning all
                   mechanics of the same type.
  - Without query : diversity-first fallback (one per type, highest score).
"""

import json
import os
import numpy as np
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

LIBRARY_FILE      = "library.json"
EMBEDDING_MODEL   = "text-embedding-3-small"
SEMANTIC_WEIGHT   = 0.7   # how much semantic similarity matters vs aggregate score
AGGREGATE_WEIGHT  = 0.3
MAX_CONTEXT_USES  = 3     # a mechanic can appear as context at most this many times per run

_client = OpenAI(
    api_key  = os.getenv("OPENAI_API_KEY", ""),
    base_url = os.getenv("OPENAI_BASE_URL", None),
)


# ── Embedding helpers ─────────────────────────────────────────────────────────

def _embed(text: str) -> list:
    """
    Call OpenAI to get a vector embedding for a piece of text.
    Returns an empty list if the call fails (graceful degradation).
    text-embedding-3-small costs ~$0.00002 per 1K tokens — essentially free.
    """
    try:
        response = _client.embeddings.create(model=EMBEDDING_MODEL, input=text)
        return response.data[0].embedding
    except Exception as e:
        print(f"[Library] Embedding failed (will use fallback): {e}")
        return []


def _cosine(a: list, b: list) -> float:
    """Cosine similarity between two embedding vectors."""
    if not a or not b:
        return 0.0
    arr_a = np.array(a, dtype=float)
    arr_b = np.array(b, dtype=float)
    denom = np.linalg.norm(arr_a) * np.linalg.norm(arr_b)
    return float(np.dot(arr_a, arr_b) / denom) if denom > 0 else 0.0


def _mechanic_text(m: dict) -> str:
    """Short text that represents a mechanic for embedding."""
    return (
        f"{m.get('mechanic_name', '')} "
        f"{m.get('mechanic_type', '')} "
        f"{m.get('description', '')} "
        f"{m.get('justification', '')}"
    )


# ── Library class ─────────────────────────────────────────────────────────────

class MechanicLibrary:
    """
    Persistent store of validated game mechanics.

    Usage:
        library = MechanicLibrary()
        library.add(mechanic_dict, scores_dict, iteration=1)

        # Semantic retrieval (preferred):
        top3 = library.retrieve(k=3, query="current game context string")

        # Diversity fallback (no query):
        top3 = library.retrieve(k=3)
    """

    def __init__(self, filepath: str = LIBRARY_FILE):
        self.filepath  = filepath
        self.mechanics = []
        self._context_use_count: dict = {}   # tracks per-run usage; not persisted to disk
        self.load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    self.mechanics = json.load(f)
                print(f"[Library] Loaded {len(self.mechanics)} mechanics from {self.filepath}")
            except Exception as e:
                print(f"[Library] Could not load library: {e}. Starting fresh.")
                self.mechanics = []
        else:
            print("[Library] No existing library found. Starting fresh.")
            self.mechanics = []

    def save(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.mechanics, f, indent=2)
        print(f"[Library] Saved {len(self.mechanics)} mechanics to {self.filepath}")

    # ── Add ───────────────────────────────────────────────────────────────────

    def add(self, mechanic: dict, scores: dict, iteration: int = 0):
        """
        Add a validated mechanic. Computes and stores an embedding for
        semantic retrieval in future iterations.
        """
        entry = {
            "mechanic_name": mechanic.get("mechanic_name", "unknown"),
            "mechanic_type": mechanic.get("mechanic_type", "other"),
            "description":   mechanic.get("description", ""),
            "justification": mechanic.get("justification", ""),
            "python_code":   mechanic.get("python_code", ""),
            "scores":        scores,
            "iteration":     iteration,
            "embedding":     _embed(_mechanic_text(mechanic)),
        }
        self.mechanics.append(entry)
        self.save()
        print(f"[Library] Added mechanic '{entry['mechanic_name']}' "
              f"(library size: {len(self.mechanics)})")

    # ── Retrieve ──────────────────────────────────────────────────────────────

    def retrieve(self, k: int = 3, query: str = None) -> list:
        """
        Retrieve up to k mechanics as context for the proposal module.

        Each mechanic can appear in context at most MAX_CONTEXT_USES times per
        run (tracked in self._context_use_count). Once all mechanics are capped,
        the counts reset so the run can continue.

        Args:
            k     : Number of mechanics to return.
            query : Free-text description of the current game context.
                    If provided, uses semantic similarity. If None, uses
                    diversity-first fallback.
        """
        if not self.mechanics:
            return []

        # Build eligible pool — exclude mechanics that hit the per-run cap
        eligible = [
            m for m in self.mechanics
            if self._context_use_count.get(m["mechanic_name"], 0) < MAX_CONTEXT_USES
        ]
        if not eligible:
            # Every mechanic has been capped — reset and start fresh
            self._context_use_count = {}
            eligible = list(self.mechanics)

        k = min(k, len(eligible))

        if query:
            selected = self._retrieve_semantic(query, k, eligible)
        else:
            selected = self._retrieve_diversity(k, eligible)

        # Record that these mechanics were used as context this iteration
        for m in selected:
            name = m["mechanic_name"]
            self._context_use_count[name] = self._context_use_count.get(name, 0) + 1

        return selected

    def _retrieve_semantic(self, query: str, k: int, pool: list) -> list:
        """
        Rank mechanics by cosine similarity to the query embedding,
        blended with their aggregate playtest score.
        A light diversity pass ensures we don't return all the same type.
        """
        query_emb = _embed(query)

        scored = []
        for m in pool:
            sim = _cosine(query_emb, m.get("embedding", []))
            # Mechanics without an embedding get a neutral similarity score
            # so they can still appear but aren't preferred
            if not m.get("embedding"):
                sim = 0.3
            agg     = m.get("scores", {}).get("aggregate", 0.5)
            combined = SEMANTIC_WEIGHT * sim + AGGREGATE_WEIGHT * agg
            scored.append((combined, m))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Diversity pass: prefer at most one mechanic per type in the top-k
        selected   = []
        seen_types = set()
        overflow   = []

        for _, m in scored:
            t = m.get("mechanic_type", "other")
            if t not in seen_types:
                selected.append(m)
                seen_types.add(t)
            else:
                overflow.append(m)
            if len(selected) == k:
                break

        # Fill remaining slots from overflow (still ranked by combined score)
        for m in overflow:
            if len(selected) >= k:
                break
            selected.append(m)

        return selected[:k]

    def _retrieve_diversity(self, k: int, pool: list) -> list:
        """
        Original fallback: one mechanic per type (highest aggregate),
        then fill by overall score.
        """
        by_type = {}
        for m in pool:
            t = m.get("mechanic_type", "other")
            by_type.setdefault(t, []).append(m)

        selected = []
        for group in by_type.values():
            if len(selected) >= k:
                break
            best = max(group, key=lambda m: m.get("scores", {}).get("aggregate", 0))
            selected.append(best)

        remaining = [m for m in pool if m not in selected]
        remaining.sort(key=lambda m: m.get("scores", {}).get("aggregate", 0), reverse=True)
        selected.extend(remaining[:k - len(selected)])

        return selected[:k]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def size(self) -> int:
        return len(self.mechanics)

    def summary(self) -> str:
        if not self.mechanics:
            return "Library is empty."
        names = [m["mechanic_name"] for m in self.mechanics]
        return f"Library has {len(self.mechanics)} mechanics: {', '.join(names)}"
