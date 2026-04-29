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
    python3 main.py --game card      # use the card game instead
    python3 main.py --iterations 5 --top-k 3
"""

import argparse
from dotenv import load_dotenv

from base_game import BaseGame
from card_game import CardGame
from mechanic_library import MechanicLibrary
from proposal_module import propose_mechanic
from compile_check import compile_check
from playtest_module import run_baseline, run_playtest_full
from verification_module import verify, ACCEPT, REVISE, DISCARD
from curriculum import Curriculum
import discarded_library

load_dotenv()


# ── Game registry: maps --game flag value to (GameInterface class, library file) ─
GAME_REGISTRY = {
    'board': (BaseGame, 'library.json',      'discarded_board.json'),
    'card':  (CardGame, 'library_card.json', 'discarded_card.json'),
}

# ── Default settings ──────────────────────────────────────────────────────────
DEFAULT_ITERATIONS = 3   # How many mechanics to try to add to the library
DEFAULT_TOP_K      = 3   # How many existing mechanics to show GPT-4 as examples
DEFAULT_GAME       = 'board'


def run_loop(n_iterations: int = DEFAULT_ITERATIONS, top_k: int = DEFAULT_TOP_K,
             game_name: str = DEFAULT_GAME):
    """
    Run the full DesignVoyager loop for n_iterations.
    Each iteration tries to produce one accepted mechanic.

    Args:
        n_iterations : number of design iterations
        top_k        : mechanics retrieved from library as context
        game_name    : key in GAME_REGISTRY ('board' or 'card')
    """
    game_class, library_file, discarded_file = GAME_REGISTRY[game_name]

    # Instantiate a throw-away game object purely to get descriptions and dummy state.
    # (No agents needed here — these are static descriptions.)
    _dummy_game   = game_class.create()
    game_skeleton = _dummy_game.get_skeleton_description()
    state_desc    = _dummy_game.get_state_description()
    dummy_state   = _dummy_game.get_dummy_state()

    library    = MechanicLibrary(filepath=library_file)
    curriculum = Curriculum()

    # Load mechanic names discarded in previous runs so Gemini won't re-propose them.
    # tried_this_run tracks names proposed in the current run (even before discard)
    # so within-run duplicates are also blocked.
    banned_names   = discarded_library.load(discarded_file)
    tried_this_run = set()

    print("\n" + "=" * 60)
    print("  DesignVoyager — Autonomous Game Mechanic Designer")
    print("=" * 60)
    print(f"  Game       : {game_name} ({game_class.__name__})")
    print(f"  Iterations : {n_iterations}")
    print(f"  Context k  : {top_k}")
    print(f"  Library    : {library.summary()}")
    print(f"  Curriculum : {curriculum.progress_str()}")
    print(f"  Banned     : {len(banned_names)} previously discarded mechanics")
    print("=" * 60 + "\n")

    # ── Baseline playtest (no mechanic) ────────────────────────────────────
    # The verifier compares each mechanic's metrics against this baseline so
    # it can reject "no-op" mechanics that look fine in absolute terms but
    # do not actually change gameplay.
    print("[Baseline] Running baseline playtest with no mechanic...")
    baseline_metrics = run_baseline(game_class=game_class)
    print(f"[Baseline] Done — playability={baseline_metrics.completed_matches}/"
          f"{baseline_metrics.total_matches}  "
          f"p1_rate={baseline_metrics.p1_win_rate:.2f}  "
          f"depth_proxy={baseline_metrics.strong_agent_win_rate - baseline_metrics.weak_agent_win_rate:+.2f}\n")

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
        all_banned = list(set(banned_names) | tried_this_run)
        mechanic = propose_mechanic(game_skeleton, retrieved,
                                    stage_prompt=curriculum.stage_prompt(),
                                    state_description=state_desc,
                                    banned_names=all_banned)
        if mechanic is None:
            print("[Loop] Proposal failed — skipping this iteration.\n")
            curriculum.on_discard()
            discarded_count += 1
            continue

        # Track that this name was tried so it won't be repeated later this run
        tried_this_run.add(mechanic.get("mechanic_name", ""))

        # ── Step 3 + 4 + 5: Compile → Playtest → Verify (with one revision) ─
        outcome = _compile_playtest_verify(mechanic, already_revised=False,
                                           game_class=game_class,
                                           dummy_state=dummy_state,
                                           baseline_metrics=baseline_metrics,
                                           stage=curriculum.stage)

        if outcome == REVISE:
            print("[Loop] Sending for revision...\n")
            revised_mechanic = _revise(mechanic, game_skeleton, retrieved,
                                       curriculum.stage_prompt(),
                                       state_description=state_desc)
            if revised_mechanic is None:
                print("[Loop] Revision failed — discarding.\n")
                discarded_library.save_name(mechanic.get("mechanic_name", ""), discarded_file)
                banned_names.append(mechanic.get("mechanic_name", ""))
                curriculum.on_discard()
                discarded_count += 1
                continue
            tried_this_run.add(revised_mechanic.get("mechanic_name", ""))
            outcome = _compile_playtest_verify(revised_mechanic, already_revised=True,
                                               game_class=game_class,
                                               dummy_state=dummy_state,
                                               baseline_metrics=baseline_metrics,
                                               stage=curriculum.stage)
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
            # Save discarded name so future runs don't re-propose it
            discarded_library.save_name(mechanic.get("mechanic_name", ""), discarded_file)
            banned_names.append(mechanic.get("mechanic_name", ""))
            curriculum.on_discard()
            discarded_count += 1
            print(f"[Loop] ✗ Discarded.")

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


def _game_name_for(game_class) -> str:
    """Map a game class to the short game_name string the verifier expects."""
    if game_class is None:
        return "board"
    cls_name = getattr(game_class, "__name__", "")
    if cls_name == "CardGame":
        return "card"
    return "board"


def _compile_playtest_verify(mechanic: dict, already_revised: bool,
                             game_class=None, dummy_state: dict = None,
                             baseline_metrics=None, stage: int = 1) -> str:
    """
    Run compile check → playtest → delta-gated verify on a mechanic.
    Returns one of: ACCEPT, REVISE, DISCARD.
    Also stores the scores in _last_scores for the caller to use.

    Args:
        game_class       : GameInterface subclass for playtesting
        dummy_state      : game-specific dummy state for compile checking
        baseline_metrics : PlaytestMetrics for the no-mechanic baseline
        stage            : current curriculum stage (1, 2, or 3)
    """
    global _last_scores

    # ── Compile check ──────────────────────────────────────────────────────
    ok, error = compile_check(mechanic, dummy_state=dummy_state)
    if not ok:
        # Send through the verifier so the rejection logging is consistent
        from playtest_module import _build_metrics, _board_cell_count
        from verification_schema import TriggerStats
        empty_phase = {
            "total_matches": 0, "completed_matches": 0, "draws": 0,
            "p1_wins": 0, "p2_wins": 0,
            "strong_wins": 0, "weak_wins": 0,
            "total_turns": 0, "multi_choice_turns": 0,
            "legal_actions_sum": 0, "covered_cells": set(),
        }
        empty_metrics = _build_metrics(empty_phase, empty_phase, _board_cell_count(game_class))
        empty_triggers = TriggerStats(0, 0, 0, 0, 0)
        decision, feedback, _ = verify(
            mechanic, empty_metrics,
            parent_metrics=baseline_metrics,
            trigger_stats=empty_triggers,
            compile_ok=False, compile_error=str(error),
            stage=stage, already_revised=already_revised,
            game_name=_game_name_for(game_class),
        )
        if decision == REVISE:
            mechanic["_revision_feedback"] = feedback
        _last_scores = {}
        return decision

    # ── Playtest (full: returns metrics, trigger stats, and simple scores) ─
    child_metrics, trigger_stats, scores = run_playtest_full(mechanic,
                                                             game_class=game_class)
    _last_scores = scores

    # ── Verify (delta-gated) ───────────────────────────────────────────────
    decision, feedback, _output = verify(
        mechanic, child_metrics,
        parent_metrics=baseline_metrics,
        trigger_stats=trigger_stats,
        compile_ok=True,
        stage=stage,
        already_revised=already_revised,
        game_name=_game_name_for(game_class),
    )
    if decision == REVISE:
        mechanic["_revision_feedback"] = feedback
    return decision


def _get_scores(mechanic: dict) -> dict:
    """Return the scores from the most recent playtest."""
    return _last_scores.copy()


def _revise(original_mechanic: dict, game_skeleton: str, retrieved: list,
            stage_prompt: str = "", state_description: str = None):
    """
    Ask GPT-4 to revise a failing mechanic, keeping the same stage prompt.

    Args:
        state_description : forwarded to propose_mechanic for game-specific LLM prompt
    """
    feedback = original_mechanic.get("_revision_feedback", "Please improve this mechanic.")
    name     = original_mechanic.get("mechanic_name", "unknown")

    revision_context = retrieved + [{
        "mechanic_name": f"{name} (PREVIOUS ATTEMPT - FAILED)",
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
                            stage_prompt=stage_prompt,
                            state_description=state_description,
                            is_revision=True)


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
    parser.add_argument(
        "--game", "-g",
        type=str,
        choices=list(GAME_REGISTRY.keys()),
        default=DEFAULT_GAME,
        help=f"Which game to design mechanics for (default: {DEFAULT_GAME})"
    )
    args = parser.parse_args()
    run_loop(n_iterations=args.iterations, top_k=args.top_k, game_name=args.game)
