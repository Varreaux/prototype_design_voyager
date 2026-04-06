"""
discarded_library.py
====================
DesignVoyager — Discarded Mechanic Names

Persistent record of mechanic names that have been discarded during
any previous run. Loaded at the start of each run and passed to the
proposal module so Gemini never re-proposes a known-bad mechanic.

Separate files per game type, matching the library.json convention:
  discarded_board.json
  discarded_card.json

To reset (start fresh): delete the relevant .json file, or call clear().
"""

import json
import os


def load(filepath: str) -> list:
    """
    Load the list of discarded mechanic names from disk.
    Returns an empty list if the file doesn't exist yet.
    """
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception as e:
        print(f"[Discarded] Could not load {filepath}: {e}. Starting fresh.")
        return []


def save_name(name: str, filepath: str):
    """
    Append a mechanic name to the discarded list on disk.
    Silently skips if the name is already recorded.
    """
    names = load(filepath)
    if name not in names:
        names.append(name)
        with open(filepath, "w") as f:
            json.dump(names, f, indent=2)
        print(f"[Discarded] Recorded '{name}' ({len(names)} total in {filepath})")


def clear(filepath: str):
    """Delete the discarded names file to start fresh."""
    if os.path.exists(filepath):
        os.remove(filepath)
        print(f"[Discarded] Cleared {filepath}")
    else:
        print(f"[Discarded] Nothing to clear ({filepath} not found)")
