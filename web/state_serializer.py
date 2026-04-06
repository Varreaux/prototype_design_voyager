"""
state_serializer.py
===================
Helpers for converting game states into JSON-safe dicts
that can be sent over WebSocket.
"""

import numpy as np


def serialize_board_state(state: dict) -> dict:
    """Convert a board game state to JSON-safe format (numpy array to list)."""
    out = {}
    for key, val in state.items():
        if isinstance(val, np.ndarray):
            out[key] = val.tolist()
        else:
            out[key] = val
    return out


def serialize_card_state(state: dict) -> dict:
    """Card game states are already JSON-safe (plain dicts and lists)."""
    return state


def serialize_state(state: dict, game_type: str) -> dict:
    """Dispatch to the right serializer based on game type."""
    if game_type == "card":
        return serialize_card_state(state)
    return serialize_board_state(state)
