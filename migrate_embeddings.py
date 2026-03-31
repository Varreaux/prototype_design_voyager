"""
migrate_embeddings.py
=====================
One-time script: re-embed all mechanics in library.json using the
Google text-embedding-004 model (768 dims) to replace the old
OpenAI text-embedding-3-small vectors (1536 dims).

Run once:  python3 migrate_embeddings.py
"""

import json
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

PROJECT  = os.getenv("GOOGLE_CLOUD_PROJECT", "voyager-api-key")
LOCATION = "us-central1"
MODEL    = "text-embedding-004"
FILE     = "library.json"

client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


def embed(text: str) -> list:
    result = client.models.embed_content(
        model=MODEL,
        contents=text,
        config=genai.types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    return result.embeddings[0].values


def mechanic_text(m: dict) -> str:
    return f"{m.get('mechanic_name', '')} {m.get('description', '')} {m.get('mechanic_type', '')}"


def main():
    with open(FILE) as f:
        data = json.load(f)

    mechanics = data if isinstance(data, list) else data.get("mechanics", [])
    total = len(mechanics)
    print(f"Re-embedding {total} mechanics...")

    for i, m in enumerate(mechanics, 1):
        name = m.get("mechanic_name", f"mechanic_{i}")
        print(f"  [{i}/{total}] {name}", end="", flush=True)
        try:
            m["embedding"] = embed(mechanic_text(m))
            print(" ✓")
        except Exception as e:
            print(f" ✗ ({e})")
            m["embedding"] = []

    # Write back in whatever shape the file was in originally
    if isinstance(data, list):
        out = mechanics
    else:
        data["mechanics"] = mechanics
        out = data

    with open(FILE, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\nDone. {FILE} updated with 768-dim embeddings.")


if __name__ == "__main__":
    main()
