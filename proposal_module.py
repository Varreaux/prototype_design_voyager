"""
proposal_module.py
==================
DesignVoyager — Proposal Module
Author: Morgan Waddington

Asks GPT-4 to propose a new game mechanic as a Python function,
with an automatic repair loop if the code has errors.
"""

import os
import ast
import json
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

API_KEY  = os.getenv("OPENAI_API_KEY", "PASTE_YOUR_KEY_HERE")
BASE_URL = os.getenv("OPENAI_BASE_URL", None)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
MODEL  = "gpt-4"
MAX_REPAIR_ATTEMPTS = 3


SYSTEM_PROMPT = """You are an expert game designer working with a Python board game framework.
The game uses a state dictionary with these keys:
  - 'board'          : a 2D numpy array of single characters ('_' = blank, 'X' = player 1, 'O' = player 2)
  - 'current_player' : integer (1 or 2)
  - 'turn'           : integer turn count

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
{
    "mechanic_name": "snake_case_name",
    "mechanic_type": "one of: scoring | movement | resource | exception | termination | other",
    "description": "One clear sentence describing what this mechanic does",
    "justification": "One sentence explaining why this improves the game",
    "python_code": "import numpy as np\\n\\ndef mechanic_name(game_state: dict) -> dict:\\n    # implementation\\n    return game_state"
}"""


def build_proposal_prompt(game_skeleton: str, retrieved_mechanics: list,
                          stage_prompt: str = "") -> str:
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
    stage_section = f"\n\n{stage_prompt}" if stage_prompt else ""
    return (
        f"Current game:\n{game_skeleton}"
        f"{mechanics_section}"
        f"{stage_section}\n\n"
        "Propose ONE new mechanic that meaningfully extends this game. "
        "Respond with raw JSON only."
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


def parse_gpt_response(text: str) -> Optional[dict]:
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
                     stage_prompt: str = "") -> Optional[dict]:
    """
    Main function: ask GPT-4 to propose a mechanic, repair if broken.

    Args:
        game_skeleton:       Plain-English description of the current game.
        retrieved_mechanics: Previously validated mechanics shown as context.
        stage_prompt:        Curriculum instruction injected into the prompt
                             (e.g. "Stage 1 — simple mechanics only").

    Returns a dict with keys: mechanic_name, mechanic_type, description,
    justification, python_code — or None if all attempts failed.
    """
    if retrieved_mechanics is None:
        retrieved_mechanics = []

    print(f"\n[Proposal] Starting... ({len(retrieved_mechanics)} prior mechanics retrieved)")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": build_proposal_prompt(
            game_skeleton, retrieved_mechanics, stage_prompt)},
    ]

    for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
        print(f"[Proposal] Attempt {attempt}/{MAX_REPAIR_ATTEMPTS}...")
        try:
            response      = client.chat.completions.create(
                model=MODEL, messages=messages, temperature=0.8, timeout=60.0
            )
            response_text = response.choices[0].message.content
        except Exception as e:
            print(f"  [Proposal] API error: {e}")
            continue

        mechanic = parse_gpt_response(response_text)
        if mechanic is None:
            messages.append({"role": "assistant", "content": response_text})
            messages.append({"role": "user", "content": "Invalid JSON. Respond ONLY with raw JSON."})
            continue

        code      = mechanic.get("python_code", "")
        is_valid, error = validate_python_syntax(code)

        if is_valid:
            print(f"  [Proposal] ✓ '{mechanic.get('mechanic_name')}'")
            return mechanic
        else:
            print(f"  [Proposal] Syntax error: {error}")
            if attempt < MAX_REPAIR_ATTEMPTS:
                messages.append({"role": "assistant", "content": response_text})
                messages.append({"role": "user", "content": build_repair_prompt(code, error)})

    print("[Proposal] ✗ All attempts failed.")
    return None
