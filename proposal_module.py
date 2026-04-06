"""
proposal_module.py
==================
DesignVoyager — Proposal Module
Author: Morgan Waddington

Asks Gemini to propose a new game mechanic as a Python function,
with an automatic repair loop if the code has errors.
"""

import os
import ast
import json
from typing import Optional
from dotenv import load_dotenv
from google import genai

load_dotenv()

PROJECT             = os.getenv("GOOGLE_CLOUD_PROJECT", "voyager-api-key")
LOCATION            = "us-central1"
MODEL               = "gemini-2.5-flash"
MAX_REPAIR_ATTEMPTS = 3


_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert game designer working with a Python game framework.
{state_description}

Your job is to propose a new game mechanic as a Python function with this exact signature:

    def mechanic_name(game_state: dict) -> dict:
        # modifies game_state and returns it
        return game_state

The function must:
1. Accept and return the game_state dict
2. Only use Python standard library + numpy (imported as np)
3. Be safe — no infinite loops, no file I/O, no network calls
4. Meaningfully change the game (not a no-op)

Always respond in this exact JSON format (raw JSON only, no markdown):
{{
    "mechanic_name": "snake_case_name",
    "mechanic_type": "one of: scoring | movement | resource | exception | termination | other",
    "description": "One clear sentence describing what this mechanic does",
    "justification": "One sentence explaining why this improves the game",
    "python_code": "import numpy as np\\n\\ndef mechanic_name(game_state: dict) -> dict:\\n    # implementation\\n    return game_state"
}}"""

# Default board-game state description used when no game_class is passed
_DEFAULT_STATE_DESCRIPTION = (
    "The game uses a state dictionary with these keys:\n"
    "  - 'board'          : a 2D numpy array of single characters "
    "('_' = blank, 'X' = player 1, 'O' = player 2)\n"
    "  - 'current_player' : integer (1 or 2)\n"
    "  - 'turn'           : integer turn count\n"
    "  - 'last_move'      : tuple (row, col) of the most recent piece placement, "
    "or None on the very first call\n"
    "  - 'extra_turn'     : boolean, default False. Set to True to give the current "
    "player an extra turn (they go again immediately instead of the opponent).\n"
    "  - 'custom_state'   : dict, default {}. Use this to store anything you need to "
    "remember between turns — scores, token counts, cooldowns, flags, etc. "
    "Example: game_state['custom_state']['p1_tokens'] = 3. "
    "This dict persists across every turn of the game.\n"
    "IMPORTANT: Only use the keys listed above. Do NOT assume any other keys exist."
)


def _build_system_prompt(state_description: str = None) -> str:
    """
    Build the LLM system prompt from the active game's state description.
    Falls back to the board-game default so existing callers still work.
    """
    desc = state_description or _DEFAULT_STATE_DESCRIPTION
    return _SYSTEM_PROMPT_TEMPLATE.format(state_description=desc)


# Keep a module-level constant for backwards compatibility with any direct imports
SYSTEM_PROMPT = _build_system_prompt()


def build_proposal_prompt(game_skeleton: str, retrieved_mechanics: list,
                          stage_prompt: str = "",
                          banned_names: list = None,
                          is_revision: bool = False) -> str:
    mechanics_section = ""
    if retrieved_mechanics:
        mechanics_section = "\n\nHere are some previously validated mechanics for reference:\n"
        for i, mech in enumerate(retrieved_mechanics, 1):
            mechanics_section += (
                f"\n--- Mechanic {i}: {mech.get('mechanic_name', 'Unknown')} ---\n"
                f"Type: {mech.get('mechanic_type', '')}\n"
                f"Description: {mech.get('description', '')}\n"
                f"Code:\n{mech.get('python_code', '')}\n"
            )
    # Combine names from retrieved library mechanics + explicitly banned names
    # (previously discarded or already tried this run) so Gemini avoids all of them.
    library_names = [m.get("mechanic_name", "") for m in retrieved_mechanics
                     if not m.get("mechanic_name", "").endswith("(PREVIOUS ATTEMPT - FAILED)")]
    all_banned = sorted(set(library_names) | set(banned_names or []))
    dedup_section = ""
    if all_banned and not is_revision:
        names_str = ", ".join(all_banned)
        dedup_section = (
            f"\n\nDo NOT propose a mechanic with any of these names (already used or "
            f"previously discarded): {names_str}. "
            "Your mechanic must have a unique name and be functionally distinct from "
            "everything listed above."
        )

    stage_section = f"\n\n{stage_prompt}" if stage_prompt else ""
    closing = (
        "Fix the mechanic shown above. Keep the same core idea but correct the issue "
        "described in the revision feedback. Respond with raw JSON only."
        if is_revision else
        "Propose ONE new mechanic that meaningfully extends this game. "
        "Respond with raw JSON only."
    )
    return (
        f"Current game:\n{game_skeleton}"
        f"{mechanics_section}"
        f"{dedup_section}"
        f"{stage_section}\n\n"
        f"{closing}"
    )


def build_repair_prompt(broken_code: str, error: str) -> str:
    return (
        f"The Python code has an error:\n\n```python\n{broken_code}\n```\n\n"
        f"Error: {error}\n\n"
        "Fix it and respond with the complete corrected JSON."
    )


def validate_python_syntax(code: str) -> tuple:
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError line {e.lineno}: {e.msg}"


def parse_response(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        end   = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text  = "\n".join(lines[start:end])
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [Proposal] JSON parse error: {e}")
        return None


def propose_mechanic(game_skeleton: str, retrieved_mechanics: list = None,
                     stage_prompt: str = "",
                     state_description: str = None,
                     banned_names: list = None,
                     is_revision: bool = False) -> Optional[dict]:
    """
    Main function: ask Gemini to propose a mechanic, repair if broken.

    Args:
        game_skeleton:       Plain-English description of the current game.
        retrieved_mechanics: Previously validated mechanics shown as context.
        stage_prompt:        Curriculum instruction injected into the prompt
                             (e.g. "Stage 1 — simple mechanics only").
        state_description:   Game-specific description of the state dict keys,
                             obtained via game_class().get_state_description().
                             If None, defaults to the board-game description.

    Returns a dict with keys: mechanic_name, mechanic_type, description,
    justification, python_code — or None if all attempts failed.
    """
    if retrieved_mechanics is None:
        retrieved_mechanics = []

    print(f"\n[Proposal] Starting... ({len(retrieved_mechanics)} prior mechanics retrieved)")

    system_prompt = _build_system_prompt(state_description)

    # Open a Vertex AI chat session with the system prompt baked in.
    # The repair loop keeps appending turns to the same session naturally.
    client = genai.Client(
        vertexai=True, project=PROJECT, location=LOCATION,
        http_options=genai.types.HttpOptions(timeout=60_000),  # 60 s, in ms
    )
    chat   = client.chats.create(
        model=MODEL,
        config=genai.types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.8,
            thinking_config=genai.types.ThinkingConfig(thinking_budget=0),
        ),
    )

    # The first message to send (subsequent turns use repair prompts)
    next_message = build_proposal_prompt(game_skeleton, retrieved_mechanics,
                                         stage_prompt, banned_names,
                                         is_revision=is_revision)

    for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
        print(f"[Proposal] Attempt {attempt}/{MAX_REPAIR_ATTEMPTS}...")
        try:
            response      = chat.send_message(next_message)
            response_text = response.text
        except Exception as e:
            print(f"  [Proposal] API error: {e}")
            continue

        mechanic = parse_response(response_text)
        if mechanic is None:
            # Ask Gemini to fix the JSON; chat history already has its reply
            next_message = "Invalid JSON. Respond ONLY with raw JSON."
            continue

        code      = mechanic.get("python_code", "")
        is_valid, error = validate_python_syntax(code)

        if is_valid:
            print(f"  [Proposal] ✓ '{mechanic.get('mechanic_name')}'")
            return mechanic
        else:
            print(f"  [Proposal] Syntax error: {error}")
            if attempt < MAX_REPAIR_ATTEMPTS:
                next_message = build_repair_prompt(code, error)

    print("[Proposal] ✗ All attempts failed.")
    return None
