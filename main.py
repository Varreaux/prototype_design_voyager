"""
main.py
=======
DesignVoyager — Full Loop

Runs the complete autonomous game mechanic design loop:

  For each iteration:
    1. Retrieve top-k mechanics from the library (context for GPT-4)
    2. Propose a new mechanic (GPT-4 via proposal_module)
    3. Compile check — does the code run without crashing?
    4. Playtest — measure playability, balance, depth (pure Python, no API cost)
    5. Verify — accept, revise, or discard
       - If REVISE: send feedback back to GPT-4, try once more
       - If ACCEPT: add to library, move to next iteration
       - If DISCARD: skip, move to next iteration

Run it:
    python3 main.py

Or with custom settings:
    python3 main.py --iterations 5 --top-k 3
"""

import argparse
from dotenv import load_dotenv

from base_game import get_skeleton_description
from mechanic_library import MechanicLibrary
from proposal_module import propose_mechanic
from compile_check import compile_check
from playtest_module import playtest
from verification_module import verify, ACCEPT, REVISE, DISCARD
from curriculum import Curriculum

load_dotenv()


# ── Default settings ──────────────────────────────────────────────────────────
DEFAULT_ITERATIONS = 3   # How many mechanics to try to add to the library
DEFAULT_TOP_K      = 3   # How many existing mechanics to show GPT-4 as examples


def run_loop(n_iterations: int = DEFAULT_ITERATIONS, top_k: int = DEFAULT_TOP_K):
    """
    Run the full DesignVoyager loop for n_iterations.
    Each iteration tries to produce one accepted mechanic.
    """
    library    = MechanicLibrary()
    curriculum = Curriculum()
    game_skeleton = get_skeleton_description()

    print("\n" + "=" * 60)
    print("  DesignVoyager — Autonomous Game Mechanic Designer")
    print("=" * 60)
    print(f"  Iterations : {n_iterations}")
    print(f"  Context k  : {top_k}")
    print(f"  Library    : {library.summary()}")
    print(f"  Curriculum : {curriculum.progress_str()}")
    print("=" * 60 + "\n")

    accepted_count = 0
    discarded_count = 0

    for iteration in range(1, n_iterations + 1):
        print(f"\n{'─' * 60}")
        print(f"  ITERATION {iteration} / {n_iterations}  |  {curriculum.progress_str()}")
        print(f"{'─' * 60}")

        # ── Step 1: Retrieve context from library ──────────────────────────
        retrieval_query = f"{game_skeleton} {curriculum.stage_name()}"
        retrieved = library.retrieve(k=top_k, query=retrieval_query)
        print(f"[Loop] Retrieved {len(retrieved)} mechanics for context.")

        # ── Step 2: Propose a mechanic ─────────────────────────────────────
        mechanic = propose_mechanic(game_skeleton, retrieved,
                                    stage_prompt=curriculum.stage_prompt())
        if mechanic is None:
            print("[Loop] Proposal failed — skipping this iteration.\n")
            curriculum.on_discard()
            discarded_count += 1
            continue

        # ── Step 3 + 4 + 5: Compile → Playtest → Verify (with one revision) ─
        outcome = _compile_playtest_verify(mechanic, already_revised=False)

        if outcome == REVISE:
            print("[Loop] Sending for revision...\n")
            revised_mechanic = _revise(mechanic, game_skeleton, retrieved,
                                       curriculum.stage_prompt())
            if revised_mechanic is None:
                print("[Loop] Revision failed — discarding.\n")
                curriculum.on_discard()
                discarded_count += 1
                continue
            outcome = _compile_playtest_verify(revised_mechanic, already_revised=True)
            mechanic = revised_mechanic

        if outcome == ACCEPT:
            scores = _get_scores(mechanic)
            library.add(mechanic, scores, iteration=iteration)
            advanced = curriculum.on_accept()
            accepted_count += 1
            print(f"[Loop] ✓ Accepted! Library now has {library.size()} mechanics.")
            if advanced:
                print(f"[Curriculum] ★ Advanced to {curriculum.stage_name()}!")
        else:
            curriculum.on_discard()
            discarded_count += 1
            print(f"[Loop] ✗ Discarded after revision.")

    # ── Final summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  Run complete!")
    print(f"  Accepted  : {accepted_count}")
    print(f"  Discarded : {discarded_count}")
    print(f"  {library.summary()}")
    print("=" * 60 + "\n")


# ── Helpers ───────────────────────────────────────────────────────────────────

# We stash the last scores here so the main loop can retrieve them after
# _compile_playtest_verify — avoids re-running playtesting.
_last_scores: dict = {}


def _compile_playtest_verify(mechanic: dict, already_revised: bool) -> str:
    """
    Run compile check → playtest → verify on a mechanic.
    Returns one of: ACCEPT, REVISE, DISCARD.
    Also stores the scores in _last_scores for the caller to use.
    """
    global _last_scores

    # ── Compile check ──────────────────────────────────────────────────────
    ok, error = compile_check(mechanic)
    if not ok:
        feedback = (
            f"The code failed a syntax or runtime check: {error}. "
            f"Please rewrite the function so it runs without errors."
        )
        if already_revised:
            return DISCARD
        # Treat a compile failure the same as REVISE so the caller can retry
        mechanic["_revision_feedback"] = feedback
        return REVISE

    # ── Playtest ───────────────────────────────────────────────────────────
    scores = playtest(mechanic)
    _last_scores = scores

    # ── Verify ────────────────────────────────────────────────────────────
    decision, feedback = verify(mechanic, scores, already_revised)
    if decision == REVISE:
        mechanic["_revision_feedback"] = feedback
    return decision


def _get_scores(mechanic: dict) -> dict:
    """Return the scores from the most recent playtest."""
    return _last_scores.copy()


def _revise(original_mechanic: dict, game_skeleton: str, retrieved: list,
            stage_prompt: str = ""):
    """
    Ask GPT-4 to revise a failing mechanic, keeping the same stage prompt.
    """
    feedback = original_mechanic.get("_revision_feedback", "Please improve this mechanic.")
    name     = original_mechanic.get("mechanic_name", "unknown")

    revision_context = retrieved + [{
        "mechanic_name": f"{name} (PREVIOUS ATTEMPT — FAILED)",
        "mechanic_type": original_mechanic.get("mechanic_type", "other"),
        "description":   original_mechanic.get("description", ""),
        "python_code":   original_mechanic.get("python_code", ""),
    }]

    skeleton_with_feedback = (
        game_skeleton
        + f"\n\nREVISION FEEDBACK for '{name}':\n{feedback}\n"
        + "Please propose a corrected version of this mechanic that fixes the issue above."
    )

    return propose_mechanic(skeleton_with_feedback, revision_context,
                            stage_prompt=stage_prompt)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DesignVoyager — autonomous mechanic designer")
    parser.add_argument(
        "--iterations", "-n",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Number of design iterations to run (default: {DEFAULT_ITERATIONS})"
    )
    parser.add_argument(
        "--top-k", "-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Mechanics retrieved from library as GPT-4 context (default: {DEFAULT_TOP_K})"
    )
    args = parser.parse_args()
    run_loop(n_iterations=args.iterations, top_k=args.top_k)
