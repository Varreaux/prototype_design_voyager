"""
curriculum.py
=============
DesignVoyager — Curriculum System

Tracks which complexity stage the agent is in and advances it
automatically after a streak of consecutive accepted mechanics.

Stages
------
Stage 1 — Simple
    Single-rule mechanics. One square or one move at a time.
    Examples: bonus squares, blocked squares, capture on adjacency.

Stage 2 — Intermediate
    Multi-square or cross-turn mechanics.
    Examples: chain reactions, territory zones, piece promotion.

Stage 3 — Complex
    Interacting systems: scoring, resource exchange, exception rules.
    Examples: scoring at end of game, resource spending, combo mechanics.

Advancement
-----------
After STREAK_TO_ADVANCE consecutive accepted mechanics the stage increases.
A discard resets the consecutive counter (but does NOT drop the stage).
"""

STREAK_TO_ADVANCE = 3   # consecutive accepts needed to move to the next stage

STAGES = {
    1: {
        "name": "Stage 1 — Simple",
        "prompt": (
            "COMPLEXITY LEVEL: Stage 1 — Simple.\n"
            "Propose ONLY simple, single-rule mechanics that affect at most one square "
            "or one move at a time.\n"
            "Good examples: a square that gives an extra turn, a square that is blocked, "
            "capturing an adjacent opponent piece, flipping a piece on placement.\n"
            "Do NOT propose scoring systems, resource tracking, or mechanics that "
            "interact with each other."
        ),
    },
    2: {
        "name": "Stage 2 — Intermediate",
        "prompt": (
            "COMPLEXITY LEVEL: Stage 2 — Intermediate.\n"
            "The library now contains validated simple mechanics. You may propose mechanics "
            "that involve multiple squares or track simple state across turns.\n"
            "Good examples: chain reactions when pieces are adjacent, a territory zone that "
            "activates after several turns, piece promotion when reaching the far row.\n"
            "Avoid full scoring systems or resource exchange — keep interactions manageable."
        ),
    },
    3: {
        "name": "Stage 3 — Complex",
        "prompt": (
            "COMPLEXITY LEVEL: Stage 3 — Complex.\n"
            "The library is rich with validated mechanics. You may now propose complex, "
            "interacting mechanics such as end-game scoring systems, resource exchange "
            "between players, exception rules, or mechanics that deliberately build on "
            "existing ones in the library.\n"
            "Aim for mechanics that create emergent strategic depth and replayability."
        ),
    },
}


class Curriculum:
    def __init__(self):
        self.stage = 1
        self.consecutive_accepts = 0

    def stage_name(self) -> str:
        return STAGES[self.stage]["name"]

    def stage_prompt(self) -> str:
        """Return the complexity instruction to inject into the GPT-4 prompt."""
        return STAGES[self.stage]["prompt"]

    def on_accept(self) -> bool:
        """
        Call this every time a mechanic is accepted.
        Returns True if the stage just advanced.
        """
        self.consecutive_accepts += 1
        if self.stage < 3 and self.consecutive_accepts >= STREAK_TO_ADVANCE:
            self.stage = min(self.stage + 1, 3)
            self.consecutive_accepts = 0
            return True
        return False

    def on_discard(self):
        """Call this every time a mechanic is discarded. Resets the streak."""
        self.consecutive_accepts = 0

    def progress_str(self) -> str:
        """Short human-readable progress string, e.g. '2/3 toward Stage 2'."""
        if self.stage == 3:
            return f"{self.stage_name()} (max stage reached)"
        next_stage = self.stage + 1
        return (
            f"{self.stage_name()}  "
            f"[{self.consecutive_accepts}/{STREAK_TO_ADVANCE} accepts toward Stage {next_stage}]"
        )
