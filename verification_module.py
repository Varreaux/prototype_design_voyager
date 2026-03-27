"""
verification_module.py
======================
DesignVoyager — Self-Verification Module

Step 4 of the loop: looks at the playtest scores and decides
what to do with the mechanic.

Three possible outcomes (from the paper):
  ACCEPT  — mechanic passes all criteria, add it to the library
  REVISE  — mechanic failed one specific thing, send back with feedback
  DISCARD — mechanic failed badly or already had its revision chance
"""

# Hard constraints (mechanic is rejected if it fails these)
MIN_PLAYABILITY = 1.0     # Every game must finish — any failure = discard
MAX_BALANCE_GAP = 0.45    # Win rate gap must be below 45%

# Soft threshold for the aggregate score
MIN_AGGREGATE   = 0.70    # Overall score must be at least 0.70

# Outcomes
ACCEPT  = "accept"
REVISE  = "revise"
DISCARD = "discard"


def verify(mechanic: dict, scores: dict, already_revised: bool = False) -> tuple:
    """
    Decide whether to accept, revise, or discard a mechanic.

    Args:
        mechanic        : dict from proposal_module
        scores          : dict from playtest_module
        already_revised : True if this mechanic already got one revision chance

    Returns:
        (decision: str, feedback: str)
        decision is one of: 'accept', 'revise', 'discard'
        feedback explains the decision (used to guide GPT-4 on revision)
    """
    name        = mechanic.get("mechanic_name", "unknown")
    playability = scores.get("playability", 0)
    balance_gap = scores.get("balance_gap", 1)
    depth       = scores.get("depth", 0)
    aggregate   = scores.get("aggregate", 0)

    print(f"[Verify] Evaluating '{name}'...")

    # ---- Hard constraint: playability (binary gate — no revision, straight discard) ----
    # Playability is either 1 (games finish) or 0 (game broken). There is no
    # meaningful revision that fixes a fundamentally unplayable mechanic.
    if playability < MIN_PLAYABILITY:
        feedback = (
            f"The mechanic caused too many games to not finish properly "
            f"(playability={playability:.0%}, minimum is {MIN_PLAYABILITY:.0%}). "
            f"The mechanic may be causing infinite loops, crashes, or removing "
            f"all valid moves. Please simplify the logic or add a safety guard."
        )
        print(f"  [Verify] ✗ DISCARD — failed playability gate ({playability:.0%})")
        return DISCARD, feedback

    # ---- Hard constraint: extreme imbalance ----
    if balance_gap > MAX_BALANCE_GAP:
        feedback = (
            f"The mechanic creates a large first-player advantage "
            f"(balance_gap={balance_gap:.0%}, maximum is {MAX_BALANCE_GAP:.0%}). "
            f"Consider making the mechanic symmetric — it should benefit "
            f"both players equally, or activate based on turn parity."
        )
        if already_revised:
            print(f"  [Verify] ✗ DISCARD — extreme imbalance after revision")
            return DISCARD, feedback
        print(f"  [Verify] → REVISE — extreme imbalance")
        return REVISE, feedback

    # ---- Soft constraint: aggregate score ----
    if aggregate < MIN_AGGREGATE:
        feedback = (
            f"The mechanic's overall score is too low (aggregate={aggregate:.2f}, "
            f"minimum is {MIN_AGGREGATE:.2f}). "
            f"Try proposing a more impactful mechanic that noticeably changes "
            f"strategy or rewards skilled play."
        )
        if already_revised:
            print(f"  [Verify] ✗ DISCARD — low aggregate score after revision")
            return DISCARD, feedback
        print(f"  [Verify] → REVISE — low aggregate score")
        return REVISE, feedback

    # ---- All checks passed ----
    print(f"  [Verify] ✓ ACCEPT — aggregate={aggregate:.2f}")
    return ACCEPT, "Mechanic accepted."
