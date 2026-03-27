"""
compile_check.py
================
DesignVoyager — Compile Check Module

Step 2 of the loop: checks that the proposed Python code actually runs
without crashing.

This catches two kinds of problems:
  1. Syntax errors   — the code is malformed Python
  2. Runtime errors  — the code runs but crashes on a real game state
"""

import ast
import copy
import numpy as np
from base_game import BOARD_SIZE, PLAYER_1


def check_syntax(code: str) -> tuple:
    """
    Check if the code is valid Python syntax.
    Returns (ok: bool, error: str)
    """
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError on line {e.lineno}: {e.msg}"


def check_runtime(code: str) -> tuple:
    """
    Try to execute the mechanic function with a dummy game state.
    Returns (ok: bool, error: str)
    """
    dummy_state = {
        'board':          np.full((BOARD_SIZE, BOARD_SIZE), '_', dtype='<U1'),
        'current_player': PLAYER_1,
        'turn':           1,
    }
    # Place a few pieces to make it realistic
    dummy_state['board'][0, 0] = 'X'
    dummy_state['board'][1, 1] = 'O'

    namespace = {"np": np, "numpy": np}
    try:
        exec(code, namespace)
    except Exception as e:
        return False, f"Code failed to load: {type(e).__name__}: {e}"

    # Find the function name (first def in the code)
    tree = ast.parse(code)
    fn_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    if not fn_names:
        return False, "No function definition found in the code."

    fn_name = fn_names[0]
    fn      = namespace.get(fn_name)
    if fn is None:
        return False, f"Function '{fn_name}' not found after exec."

    try:
        result = fn(copy.deepcopy(dummy_state))
    except Exception as e:
        return False, f"Function crashed at runtime: {type(e).__name__}: {e}"

    if not isinstance(result, dict):
        return False, f"Function must return a dict but returned {type(result).__name__}."

    if 'board' not in result:
        return False, "Returned dict is missing the 'board' key."

    return True, ""


def compile_check(mechanic: dict) -> tuple:
    """
    Full compile check: syntax + runtime.

    Args:
        mechanic : dict from proposal_module (must have 'python_code')

    Returns:
        (ok: bool, error: str)
    """
    code = mechanic.get("python_code", "")
    name = mechanic.get("mechanic_name", "unknown")

    print(f"[CompileCheck] Checking '{name}'...")

    ok, err = check_syntax(code)
    if not ok:
        print(f"  [CompileCheck] ✗ Syntax error: {err}")
        return False, err

    ok, err = check_runtime(code)
    if not ok:
        print(f"  [CompileCheck] ✗ Runtime error: {err}")
        return False, err

    print(f"  [CompileCheck] ✓ Passed")
    return True, ""


def load_mechanic_fn(code: str):
    """
    Load the mechanic Python function from a code string.
    Returns the callable function object, or None on failure.
    """
    namespace = {"np": np, "numpy": np}
    exec(code, namespace)
    tree     = ast.parse(code)
    fn_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    if not fn_names:
        return None
    return namespace.get(fn_names[0])
