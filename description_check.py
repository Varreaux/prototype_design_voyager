"""
description_check.py
====================
DesignVoyager, semantic alignment check between a mechanic's description
and its python code.

This is the 4th gate in the verifier, added after we discovered that the
LLM sometimes writes code that does not match its own description (for
example, code that re-adds the played card to the score even though the
description does not mention any double scoring, which silently passed
the existing playtest gates because metrics still moved).

Public API:
    check_description_matches_code(description, code, game_name)
        -> (matches: bool, issue: str)

The check fails open: if the LLM call itself errors or returns garbage,
we return matches=True so a transient infrastructure problem cannot block
otherwise-valid mechanics from entering the library. The infra failure is
printed so it shows up in the run log.
"""

from __future__ import annotations

import json
import os
from typing import Tuple

from dotenv import load_dotenv
from google import genai

load_dotenv()

PROJECT  = os.getenv("GOOGLE_CLOUD_PROJECT", "voyager-api-key")
LOCATION = "us-central1"
MODEL    = "gemini-2.5-flash"


# Game-specific notes the reviewer needs to judge alignment correctly.
# The big one is the card game's perform_move side effect: it ALREADY adds
# the played card to the score, so a mechanic that also does that is
# double-counting (the actual bug we are trying to catch).
_GAME_CONTEXT = {
    "board": (
        "perform_move places the current player's piece at the chosen square "
        "('X' for player 1, 'O' for player 2). The mechanic function runs "
        "AFTER that placement, on a state dict that already reflects it."
    ),
    "card": (
        "perform_move pops the chosen card from the current player's hand "
        "and ADDS the card's value to that player's score. The mechanic "
        "function runs AFTER that step, on a state dict where the score "
        "already includes the played card. If the description does not "
        "mention any extra or double scoring of the played card, then code "
        "that does scores[current_player] += played_card inside the mechanic "
        "is double-counting and should be flagged as a mismatch."
    ),
}


_REVIEW_PROMPT_TEMPLATE = """\
You are reviewing one game mechanic to make sure its python code matches \
the one-sentence description. Both the description and the code came from \
the same LLM, but the code can drift from the description in ways that \
silently break the game.

Game-specific context (very important):
{game_context}

Mechanic description:
{description}

Mechanic code:
```python
{code}
```

Decide whether the code faithfully implements the description. Things that \
count as a MISMATCH:
- The code adds, subtracts, or modifies a value that the description does \
not mention.
- The code's trigger condition is different from the description's trigger \
condition (for example, the description says "lower than" but the code \
checks "lower than or equal to").
- The code applies the effect to the wrong player.
- The code has side effects on hands, scores, or custom_state that the \
description does not describe.
- The code modifies the game in the wrong direction (for example, awards \
points where the description says it should subtract them).

Things that are NOT mismatches and should be ignored:
- Bookkeeping writes to custom_state that are needed to remember information \
between turns.
- Defensive null checks or initialisation code.
- Differences in variable names or code style.

Respond with raw JSON only, in this exact shape:

{{"matches": true}}

if the code faithfully implements the description, or

{{"matches": false, "issue": "one sentence explaining the specific mismatch"}}

if there is a mismatch."""


def _build_prompt(description: str, code: str, game_name: str) -> str:
    context = _GAME_CONTEXT.get(game_name, _GAME_CONTEXT["board"])
    return _REVIEW_PROMPT_TEMPLATE.format(
        game_context=context,
        description=description.strip(),
        code=code.strip(),
    )


def _parse_response(text: str) -> Tuple[bool, str]:
    """Parse the JSON response from the reviewer model. Fail open on garbage."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text  = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return True, ""    # fail open
    if not isinstance(payload, dict):
        return True, ""
    matches = bool(payload.get("matches", True))
    issue   = str(payload.get("issue", "")).strip() if not matches else ""
    return matches, issue


def check_description_matches_code(description: str, code: str,
                                    game_name: str = "board") -> Tuple[bool, str]:
    """
    Ask Gemini whether the mechanic code matches its own description.

    Args:
        description : the one-sentence description from the proposal.
        code        : the python_code string from the proposal.
        game_name   : "board" or "card", used to pick the right perform_move
                      context note. Unknown values fall back to "board".

    Returns:
        (matches, issue). matches is True if alignment looks fine OR if the
        check itself failed (fail-open). issue is a short string explaining
        the mismatch when matches is False, otherwise empty.
    """
    if not description or not code:
        return True, ""    # nothing to compare, fail open

    prompt = _build_prompt(description, code, game_name)

    try:
        client = genai.Client(
            vertexai=True, project=PROJECT, location=LOCATION,
            http_options=genai.types.HttpOptions(timeout=60_000),
        )
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.0,
                thinking_config=genai.types.ThinkingConfig(thinking_budget=0),
            ),
        )
        text = (getattr(response, "text", None) or "").strip()
    except Exception as e:
        print(f"  [DescriptionCheck] LLM call failed, accepting: {type(e).__name__}: {e}")
        return True, ""

    if not text:
        print("  [DescriptionCheck] empty response, accepting")
        return True, ""

    matches, issue = _parse_response(text)
    if not matches:
        print(f"  [DescriptionCheck] MISMATCH: {issue}")
    else:
        print(f"  [DescriptionCheck] aligned")
    return matches, issue


# ── Quick sanity check ───────────────────────────────────────────────────────
if __name__ == "__main__":
    # Known-buggy: re-adds played_card unconditionally
    buggy_desc = (
        "If a player plays a card with the exact same value as the last card "
        "played by their opponent, the opponent's score is reset to 0."
    )
    buggy_code = (
        "def exact_match_score_reset(game_state):\n"
        "    cp = game_state['current_player']\n"
        "    op = 1 if cp == 2 else 2\n"
        "    played = game_state['last_played']\n"
        "    if game_state['turn'] > 1 and played is not None:\n"
        "        cs = game_state.setdefault('custom_state', {})\n"
        "        last_by = cs.setdefault('last_played_by_player', {1: None, 2: None})\n"
        "        if last_by[op] == played:\n"
        "            game_state['scores'][op] = 0\n"
        "        last_by[cp] = played\n"
        "    if played is not None:\n"
        "        game_state['scores'][cp] += played   # <-- bug: double-counts\n"
        "    return game_state\n"
    )
    print("Buggy mechanic:")
    print(check_description_matches_code(buggy_desc, buggy_code, "card"))
