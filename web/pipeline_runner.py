"""
pipeline_runner.py
==================
Web-adapted pipeline loop for DesignVoyager.

Mirrors the logic from demo_main.py but emits structured events
instead of printing Rich output. Events go into a thread-safe
queue that the FastAPI WebSocket handler reads from.
"""

import sys
import os
import json
import queue
import contextlib
import io
import threading
import concurrent.futures
import multiprocessing as mp

# Add parent directory to path so we can import the pipeline modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base_game import BaseGame
from card_game import CardGame
from mechanic_library import MechanicLibrary
from proposal_module import propose_mechanic
from compile_check import compile_check
from playtest_module import (
    run_baseline,
    run_playtest_full,
    run_single_game_recorded,
    load_mechanic_fn,
    _build_metrics,
    _board_cell_count,
)
from verification_module import verify, ACCEPT, REVISE, DISCARD
from verification_schema import TriggerStats
from curriculum import Curriculum
import discarded_library


GAME_REGISTRY = {
    'board': (BaseGame, 'library.json',      'discarded_board.json'),
    'card':  (CardGame, 'library_card.json', 'discarded_card.json'),
}


def _demo_replay_subprocess(game_name: str, mechanic_code: str, result_queue):
    """
    Top-level so multiprocessing can pickle it. Runs the recorded demo game
    in a child process that we can kill via Process.terminate() if the
    Gemini-generated mechanic spins (infinite loop, runaway search, etc.).

    Threads were used here previously, but Python threads can't be cancelled
    once started — a runaway mechanic would wedge the worker thread and the
    parent's ThreadPoolExecutor.__exit__ shutdown(wait=True) would block on
    it forever. A subprocess can be sent SIGTERM cleanly.
    """
    try:
        # The child process needs the project root on sys.path to import
        # the pipeline modules. Mirrors the parent's path setup at the top
        # of this file.
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if proj_root not in sys.path:
            sys.path.insert(0, proj_root)
        from compile_check import load_mechanic_fn as _load
        from playtest_module import run_single_game_recorded as _run
        from base_game import BaseGame as _BG
        from card_game import CardGame as _CG
        gc = _CG if game_name == 'card' else _BG
        fn = _load(mechanic_code)
        out = _run(mechanic_fn=fn, game_class=gc)
        result_queue.put(("ok", out))
    except BaseException as e:
        # BaseException so SystemExit / KeyboardInterrupt propagate the same
        # error path. Returning the message rather than raising means the
        # parent doesn't have to unpickle a (possibly nonpicklable) exc.
        result_queue.put(("err", f"{type(e).__name__}: {e}"))

# Single file that accumulates accepted-mechanic card records across all runs.
# Each record includes the replay data needed to render the library browser.
LIBRARY_CARDS_FILE = "library_cards.json"


def _save_library_card(card: dict):
    """
    Append a mechanic card record to the library cards file.

    Two robustness fixes vs the original:
    1. `default=str` so json.dump doesn't raise on tuple keys inside
       custom_state (mechanics like diagonal_blockade store
       (row, col) tuples as keys in blocked_squares).
    2. Atomic write via temp file + os.replace, so a failed dump
       leaves the original file intact rather than truncating it
       to a partial, unparseable state.
    """
    cards = []
    try:
        with open(LIBRARY_CARDS_FILE, 'r') as f:
            cards = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cards = []
    cards.append(card)
    tmp_path = LIBRARY_CARDS_FILE + ".tmp"
    try:
        with open(tmp_path, 'w') as f:
            json.dump(cards, f, indent=2, default=str)
        os.replace(tmp_path, LIBRARY_CARDS_FILE)
    except Exception:
        # Best-effort cleanup of the temp file. Never crash the pipeline
        # over a file write.
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


class EventEmitter:
    """
    Pushes structured events onto a thread-safe queue.
    The WebSocket handler reads from this queue and sends JSON to the browser.
    """

    def __init__(self, event_queue: queue.Queue):
        self._queue = event_queue

    def emit(self, event_type: str, data: dict = None):
        self._queue.put({"type": event_type, "data": data or {}})


@contextlib.contextmanager
def _suppress_stdout():
    """Silence print() from the underlying pipeline modules."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def run_web_pipeline(emitter: EventEmitter, game_name: str,
                     n_iterations: int, top_k: int,
                     stop_event: threading.Event = None):
    """
    Run the full DesignVoyager pipeline, emitting events at each step.

    Args:
        emitter      : EventEmitter that pushes to the WebSocket queue
        game_name    : "board" or "card"
        n_iterations : how many design iterations
        top_k        : how many library mechanics to retrieve as context
        stop_event   : set this to signal early termination
    """
    if game_name not in GAME_REGISTRY:
        emitter.emit("error", {"message": f"Unknown game: {game_name}"})
        return

    game_class, library_file, discarded_file = GAME_REGISTRY[game_name]

    # Set up game descriptions
    dummy_game  = game_class.create()
    skeleton    = dummy_game.get_skeleton_description()
    state_desc  = dummy_game.get_state_description()
    dummy_state = dummy_game.get_dummy_state()

    with _suppress_stdout():
        library = MechanicLibrary(filepath=library_file)
    curriculum = Curriculum()

    banned_names   = discarded_library.load(discarded_file)
    tried_this_run = set()

    # Welcome
    emitter.emit("welcome", {
        "game_name":          game_name,
        "game_class":         game_class.__name__,
        "iterations":         n_iterations,
        "top_k":              top_k,
        "library_size":       library.size(),
        "library_mechanics":  [m["mechanic_name"] for m in library.mechanics],
        "curriculum_stage":   curriculum.stage_name(),
        "curriculum_progress": curriculum.progress_str(),
        "banned_count":       len(banned_names),
    })

    # ── Baseline playtest (no mechanic) ─────────────────────────────────
    # Tells the verifier what "the game with no mechanic" looks like, so it
    # can reject mechanics that don't change anything (delta-gated check).
    from playtest_module import N_GAMES_BALANCE, N_GAMES_DEPTH
    emitter.emit("baseline_start", {
        "total_games": N_GAMES_BALANCE + N_GAMES_DEPTH,
        "balance_games": N_GAMES_BALANCE,
        "depth_games":   N_GAMES_DEPTH,
    })

    def _baseline_progress(phase, completed, total):
        emitter.emit("baseline_progress", {
            "phase":     phase,
            "completed": completed,
            "total":     total,
        })

    with _suppress_stdout():
        baseline_metrics = run_baseline(game_class=game_class, use_signal=False,
                                        progress_cb=_baseline_progress)
    emitter.emit("baseline_result", {
        "playability": (baseline_metrics.completed_matches /
                        max(baseline_metrics.total_matches, 1)),
        "p1_win_rate":   baseline_metrics.p1_win_rate,
        "p2_win_rate":   baseline_metrics.p2_win_rate,
        "balance_gap":   abs(baseline_metrics.p1_win_rate - baseline_metrics.p2_win_rate),
        "depth":         max(0.0, min(1.0, baseline_metrics.strong_agent_win_rate
                                            - baseline_metrics.weak_agent_win_rate)),
        "decisiveness":  max(0.0, 1.0 - baseline_metrics.draw_rate),
        "agency":        (baseline_metrics.multi_choice_turns /
                          max(baseline_metrics.total_turns, 1)),
        "avg_game_length": baseline_metrics.avg_game_length,
        "total_matches": baseline_metrics.total_matches,
    })

    accepted_count  = 0
    discarded_count = 0

    for iteration in range(1, n_iterations + 1):
        # Check for early stop
        if stop_event and stop_event.is_set():
            emitter.emit("error", {"message": "Run stopped by user."})
            break

        emitter.emit("iteration_start", {
            "iteration":  iteration,
            "total":      n_iterations,
            "stage_name": curriculum.stage_name(),
        })

        # Step 1: Retrieve
        retrieval_query = f"{skeleton} {curriculum.stage_name()}"
        with _suppress_stdout():
            retrieved = library.retrieve(k=top_k, query=retrieval_query)

        emitter.emit("retrieve", {
            "mechanic_names": [m["mechanic_name"] for m in retrieved],
            "count":          len(retrieved),
        })

        # Step 2: Propose
        all_banned = list(set(banned_names) | tried_this_run)
        emitter.emit("propose_start", {"context_count": len(retrieved)})

        def _propose_stream(accumulated):
            emitter.emit("propose_stream", {"text": accumulated})

        with _suppress_stdout():
            mechanic = propose_mechanic(
                skeleton, retrieved,
                stage_prompt=curriculum.stage_prompt(),
                state_description=state_desc,
                banned_names=all_banned,
                stream_cb=_propose_stream,
                library_roster=library.mechanics,
            )

        if mechanic is None:
            emitter.emit("propose_result", {"failed": True})
            emitter.emit("verify_result", {
                "decision": DISCARD,
                "feedback": "Proposal failed completely.",
            })
            curriculum.on_discard()
            discarded_count += 1
            continue

        tried_this_run.add(mechanic.get("mechanic_name", ""))

        emitter.emit("propose_result", {
            "failed":        False,
            "mechanic_name": mechanic.get("mechanic_name", "unknown"),
            "mechanic_type": mechanic.get("mechanic_type", ""),
            "description":   mechanic.get("description", ""),
            "python_code":   mechanic.get("python_code", ""),
        })

        # Steps 3-5: Compile, Playtest, Verify
        decision, scores, replay_data = _compile_playtest_verify(
            emitter, mechanic, already_revised=False,
            game_class=game_class, game_name=game_name,
            dummy_state=dummy_state,
            baseline_metrics=baseline_metrics,
            stage=curriculum.stage,
        )

        # Revision path
        if decision == REVISE:
            # Tell the UI the first attempt needs revision before starting it
            emitter.emit("verify_result", {
                "decision": REVISE,
                "feedback": mechanic.get("_revision_feedback", ""),
                "scores":   scores,
            })

            feedback     = mechanic.get("_revision_feedback", "Please improve this mechanic.")
            revision_ctx = retrieved + [{
                "mechanic_name": f"{mechanic['mechanic_name']} (PREVIOUS ATTEMPT - FAILED)",
                "mechanic_type": mechanic.get("mechanic_type", "other"),
                "description":   mechanic.get("description", ""),
                "python_code":   mechanic.get("python_code", ""),
            }]
            revised_skeleton = (
                skeleton
                + f"\n\nREVISION FEEDBACK for '{mechanic['mechanic_name']}':\n{feedback}\n"
                + "Please propose a corrected version that fixes the issue described above."
            )

            emitter.emit("revision_start", {
                "mechanic_name": mechanic.get("mechanic_name", "unknown"),
            })

            def _revise_stream(accumulated):
                emitter.emit("propose_stream", {"text": accumulated, "revision": True})

            with _suppress_stdout():
                revised = propose_mechanic(
                    revised_skeleton, revision_ctx,
                    stage_prompt=curriculum.stage_prompt(),
                    state_description=state_desc,
                    banned_names=all_banned,
                    is_revision=True,
                    stream_cb=_revise_stream,
                )

            if revised is None:
                emitter.emit("revision_result", {"failed": True})
                emitter.emit("verify_result", {
                    "decision": DISCARD,
                    "feedback": "Revision failed.",
                })
                discarded_library.save_name(mechanic.get("mechanic_name", ""), discarded_file)
                banned_names.append(mechanic.get("mechanic_name", ""))
                curriculum.on_discard()
                discarded_count += 1
                continue

            tried_this_run.add(revised.get("mechanic_name", ""))
            emitter.emit("revision_result", {
                "failed":        False,
                "mechanic_name": revised.get("mechanic_name", "unknown"),
                "mechanic_type": revised.get("mechanic_type", ""),
                "description":   revised.get("description", ""),
                "python_code":   revised.get("python_code", ""),
            })

            mechanic = revised
            decision, scores, replay_data = _compile_playtest_verify(
                emitter, mechanic, already_revised=True,
                game_class=game_class, game_name=game_name,
                dummy_state=dummy_state,
                baseline_metrics=baseline_metrics,
                stage=curriculum.stage,
            )

        # Final verdict
        emitter.emit("verify_result", {
            "decision": decision,
            "feedback": mechanic.get("_revision_feedback", ""),
            "scores":   scores,
        })

        if decision == ACCEPT:
            # Embedding-similarity reject: catch near-duplicates that the
            # roster + name-ban couldn't dissuade Gemini from cloning. The
            # library already stores embeddings, so this is one extra
            # cosine-comparison sweep per accept.
            with _suppress_stdout():
                twin, sim = library.find_similar(mechanic, threshold=0.92)
            if twin is not None:
                twin_name = twin.get("mechanic_name", "?")
                emitter.emit("verify_result", {
                    "decision": DISCARD,
                    "feedback": (f"Discarded as near-duplicate of "
                                 f"'{twin_name}' (embedding cosine "
                                 f"{sim:.3f} ≥ 0.92)."),
                    "scores":   scores,
                })
                discarded_library.save_name(mechanic.get("mechanic_name", ""), discarded_file)
                banned_names.append(mechanic.get("mechanic_name", ""))
                curriculum.on_discard()
                discarded_count += 1
                continue
            with _suppress_stdout():
                library.add(mechanic, scores, iteration=iteration)
            # Persist card data and notify the Library browser
            if replay_data:
                card = {
                    "game_type":   game_name,
                    "mechanic_name": mechanic.get("mechanic_name", ""),
                    "description":   mechanic.get("description", ""),
                    "scores":        scores,
                    "replay":        replay_data,
                    "iteration":     iteration,
                }
                _save_library_card(card)
                emitter.emit("mechanic_accepted", card)
            advanced = curriculum.on_accept()
            accepted_count += 1
            if advanced:
                emitter.emit("curriculum_advance", {
                    "new_stage_name": curriculum.stage_name(),
                })
        else:
            discarded_library.save_name(mechanic.get("mechanic_name", ""), discarded_file)
            banned_names.append(mechanic.get("mechanic_name", ""))
            curriculum.on_discard()
            discarded_count += 1

    # Run complete
    emitter.emit("run_complete", {
        "accepted_count":  accepted_count,
        "discarded_count": discarded_count,
        "library_size":    library.size(),
        "mechanic_names":  [m["mechanic_name"] for m in library.mechanics],
    })


def _empty_phase():
    return {
        "total_matches": 0, "completed_matches": 0, "draws": 0,
        "p1_wins": 0, "p2_wins": 0,
        "strong_wins": 0, "weak_wins": 0,
        "total_turns": 0, "multi_choice_turns": 0,
        "legal_actions_sum": 0, "covered_cells": set(),
    }


def _compile_playtest_verify(emitter, mechanic, already_revised,
                              game_class, game_name, dummy_state,
                              baseline_metrics, stage):
    """
    Run compile check, playtest, and delta-gated verify. Emits events for each step.
    Returns (decision, scores, replay_data_dict).
    replay_data_dict is None when there is no usable replay (compile fail or fast-fail).
    """
    # Compile check
    with _suppress_stdout():
        ok, error = compile_check(mechanic, dummy_state=dummy_state)

    emitter.emit("compile_result", {
        "passed": ok,
        "error":  error if not ok else None,
    })

    if not ok:
        # Run mechanic through the verifier with empty metrics so the rejection
        # is logged consistently. The verifier will treat this as syntax_failure.
        empty_metrics = _build_metrics(_empty_phase(), _empty_phase(),
                                       _board_cell_count(game_class))
        empty_triggers = TriggerStats(0, 0, 0, 0, 0)
        with _suppress_stdout():
            decision, feedback, output = verify(
                mechanic, empty_metrics,
                parent_metrics=baseline_metrics,
                trigger_stats=empty_triggers,
                compile_ok=False, compile_error=str(error),
                stage=stage, already_revised=already_revised,
                game_name=game_name,
            )
        if decision == REVISE:
            mechanic["_revision_feedback"] = feedback
        return decision, {}, None

    # Record one game for animated replay immediately after compile check passes.
    # Emit demo_replay_start so the UI knows the pipeline is alive in this
    # otherwise-silent stretch (compile passed, but no events fire until the
    # full playtest starts below). If the recorded game raises, surface it
    # via error_inline so the user knows what went wrong instead of seeing a
    # dead UI.
    emitter.emit("demo_replay_start", {
        "mechanic_name": mechanic.get("mechanic_name", "unknown"),
    })
    replay_completed = True
    replay_data_dict = None
    # Hard wall-clock cap on the recorded game. The full playtest below
    # already has its own per-game timeout, but the recorded game did
    # not, so a Gemini mechanic with a slow path or near-infinite loop
    # could wedge the pipeline indefinitely (this happened once on
    # adjacent_blockade).
    DEMO_REPLAY_TIMEOUT = 30  # seconds
    proc = None
    try:
        # Run the recorded game in a *subprocess* (not a thread) so that a
        # Gemini-generated mechanic with an infinite loop can be killed via
        # Process.terminate(). With ThreadPoolExecutor, even after the
        # 30-second future.result(timeout=…) raised, exiting the executor
        # context blocked on shutdown(wait=True) waiting for the runaway
        # worker thread to finish — which it never did. That manifested as
        # the dashboard sitting on "Recording demo replay for X..." for
        # hours.
        #
        # Use spawn explicitly so behaviour is consistent across macOS
        # (default spawn) and Linux (default fork). spawn re-imports the
        # module in the child, so the worker is a top-level function.
        ctx = mp.get_context("spawn")
        result_q = ctx.Queue()
        proc = ctx.Process(
            target=_demo_replay_subprocess,
            args=(game_name, mechanic.get("python_code", ""), result_q),
            daemon=True,
        )
        proc.start()
        proc.join(timeout=DEMO_REPLAY_TIMEOUT)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            if proc.is_alive():
                # SIGTERM ignored (rare for pure Python). Last resort.
                proc.kill()
                proc.join(timeout=1)
            raise concurrent.futures.TimeoutError(
                f"Demo replay exceeded {DEMO_REPLAY_TIMEOUT}s")
        # The child finished; pick up its result.
        try:
            status, payload = result_q.get_nowait()
        except Exception:
            raise RuntimeError("Demo replay subprocess returned no result")
        if status == "err":
            raise RuntimeError(payload)
        replay = payload
        replay_completed = replay.get("completed", True)
        replay_data_dict = {
            "game_type":            game_name,
            "mechanic_name":        mechanic.get("mechanic_name", ""),
            "mechanic_description": mechanic.get("description", ""),
            "initial_state":        replay["initial_state"],
            "moves":                replay["moves"],
            "winner":               replay["winner"],
            "total_turns":          replay["turns"],
        }
        emitter.emit("replay_data", replay_data_dict)
    except concurrent.futures.TimeoutError:
        emitter.emit("error_inline", {
            "message": (f"Demo replay timed out after {DEMO_REPLAY_TIMEOUT}s "
                        f"(subprocess terminated). Skipping replay and "
                        f"proceeding to playtest."),
        })
    except Exception as e:
        # Replay is nice-to-have, don't crash the pipeline -- but DO tell the UI.
        emitter.emit("error_inline", {
            "message": f"Demo replay failed: {type(e).__name__}: {e}",
        })
    finally:
        # Belt-and-suspenders: if we left the subprocess alive due to an
        # exception path that didn't go through the timeout branch, kill
        # it now so we don't pile up zombies across iterations.
        if proc is not None and proc.is_alive():
            try:
                proc.terminate()
                proc.join(timeout=1)
                if proc.is_alive():
                    proc.kill()
            except Exception:
                pass
    emitter.emit("demo_replay_done", {})

    # Fast-fail: recorded game never finished -> unplayable. Skip full playtest.
    if not replay_completed:
        scores = {"playability": 0.0, "balance_gap": 1.0,
                  "depth": 0.0, "aggregate": 0.0}
        emitter.emit("playtest_start", {
            "mechanic_name": mechanic.get("mechanic_name", "unknown"),
        })
        emitter.emit("playtest_result", {
            "scores":         scores,
            "delta_metrics":  {},
            "absolute_metrics": {"playability": 0.0, "balance_gap": 1.0,
                                 "depth": 0.0, "decisiveness": 0.0,
                                 "agency": 0.0, "trigger_rate": 0.0},
            "trigger_stats":  {"trigger_rate": 0.0, "triggered_matches": 0,
                               "total_matches": 0},
        })
        # Send through verifier with zero-metrics so behavioral gate flags it
        zero_metrics = _build_metrics(_empty_phase(), _empty_phase(),
                                      _board_cell_count(game_class))
        zero_triggers = TriggerStats(0, 0, 0, 0, 0)
        with _suppress_stdout():
            decision, feedback, _ = verify(
                mechanic, zero_metrics,
                parent_metrics=baseline_metrics,
                trigger_stats=zero_triggers,
                compile_ok=True, stage=stage,
                already_revised=already_revised,
            )
        if decision == REVISE:
            mechanic["_revision_feedback"] = feedback
        return decision, scores, None

    # Full playtest (use_signal=False since we run in a background thread)
    emitter.emit("playtest_start", {
        "mechanic_name": mechanic.get("mechanic_name", "unknown"),
    })

    def _play_progress(phase, completed, total):
        emitter.emit("playtest_progress", {
            "phase":     phase,
            "completed": completed,
            "total":     total,
        })

    with _suppress_stdout():
        child_metrics, trigger_stats, scores = run_playtest_full(
            mechanic, game_class=game_class, use_signal=False,
            progress_cb=_play_progress,
        )

    # Verify (delta-gated). The verifier returns the full output dict so we
    # can show the user the deltas and the relative gain.
    with _suppress_stdout():
        decision, feedback, output = verify(
            mechanic, child_metrics,
            parent_metrics=baseline_metrics,
            trigger_stats=trigger_stats,
            compile_ok=True, stage=stage,
            already_revised=already_revised,
            game_name=game_name,
        )

    # Emit a rich playtest_result that includes the delta info the UI wants.
    abs_metrics = output.get("absolute_metrics", {}) if output else {}
    delta_metrics = output.get("delta_metrics", {}) if output else {}
    emitter.emit("playtest_result", {
        "scores":           scores,                    # legacy bars stay green
        "absolute_metrics": abs_metrics,               # new: agency, decisiveness, trigger_rate
        "delta_metrics":    delta_metrics,             # new: delta_depth, delta_balance_gap, etc
        "relative_score":   output.get("relative_score", 0.0) if output else 0.0,
        "overall_score":    output.get("overall_score", 0.0) if output else 0.0,
        "trigger_stats":    output.get("trigger_stats", {}) if output else {},
        "stage_threshold":  (0.015 if stage <= 1 else (0.012 if stage == 2 else 0.008)),
        "failure_modes":    output.get("failure_modes", []) if output else [],
    })

    # Stash feedback on the mechanic for any non-accept outcome so the final
    # verdict can show the actual failure reason (extreme_imbalance, etc.)
    # rather than the generic "Could not produce a working mechanic" fallback.
    if feedback and decision != ACCEPT:
        mechanic["_revision_feedback"] = feedback

    return decision, scores, replay_data_dict
