"""
migrate_embeddings.py
=====================
One-time script: re-embed all mechanics in library.json using the
Google text-embedding-004 model (768 dims) to replace the old
OpenAI text-embedding-3-small vectors (1536 dims).

Run once:  python3 migrate_embeddings.py
"""

import json
from mechanic_library import _embed, _mechanic_text, LIBRARY_FILE


def main():
    with open(LIBRARY_FILE) as f:
        data = json.load(f)

    mechanics = data if isinstance(data, list) else data.get("mechanics", [])
    total = len(mechanics)
    print(f"Re-embedding {total} mechanics...")

    for i, m in enumerate(mechanics, 1):
        name = m.get("mechanic_name", f"mechanic_{i}")
        print(f"  [{i}/{total}] {name}", end="", flush=True)
        try:
            m["embedding"] = _embed(_mechanic_text(m))
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

    with open(LIBRARY_FILE, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\nDone. {LIBRARY_FILE} updated with 768-dim embeddings.")


if __name__ == "__main__":
    main()
