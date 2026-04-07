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

# Add parent directory to path so we can import the pipeline modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from base_game import BaseGame
from card_game import CardGame
from mechanic_library import MechanicLibrary
from proposal_module import propose_mechanic
from compile_check import compile_check
from playtest_module import playtest, run_single_game_recorded, load_mechanic_fn
from verification_module import verify, ACCEPT, REVISE, DISCARD
from curriculum import Curriculum
import discarded_library


GAME_REGISTRY = {
    'board': (BaseGame, 'library.json',      'discarded_board.json'),
    'card':  (CardGame, 'library_card.json', 'discarded_card.json'),
}

# Single file that accumulates accepted-mechanic card records across all runs.
# Each record includes the replay data needed to render the library browser.
LIBRARY_CARDS_FILE = "library_cards.json"


def _save_library_card(card: dict):
    """Append a mechanic card record to the library cards file."""
    cards = []
    try:
        with open(LIBRARY_CARDS_FILE, 'r') as f:
            cards = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cards = []
    cards.append(card)
    try:
        with open(LIBRARY_CARDS_FILE, 'w') as f:
            json.dump(cards, f, indent=2)
    except Exception:
        pass  # Never crash the pipeline over a file write


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

        with _suppress_stdout():
            mechanic = propose_mechanic(
                skeleton, retrieved,
                stage_prompt=curriculum.stage_prompt(),
                state_description=state_desc,
                banned_names=all_banned,
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

            with _suppress_stdout():
                revised = propose_mechanic(
                    revised_skeleton, revision_ctx,
                    stage_prompt=curriculum.stage_prompt(),
                    state_description=state_desc,
                    banned_names=all_banned,
                    is_revision=True,
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
            )

        # Final verdict
        emitter.emit("verify_result", {
            "decision": decision,
            "feedback": mechanic.get("_revision_feedback", ""),
            "scores":   scores,
        })

        if decision == ACCEPT:
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


def _compile_playtest_verify(emitter, mechanic, already_revised,
                              game_class, game_name, dummy_state):
    """
    Run compile check, playtest, and verify. Emits events for each step.
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
        if already_revised:
            return DISCARD, {}, None
        mechanic["_revision_feedback"] = f"The code crashed: {error}. Please rewrite to fix this."
        return REVISE, {}, None

    # Record one game for animated replay immediately after compile check passes.
    # Uses two MCTS agents at 50 simulations each for intelligent-looking play.
    # IMPORTANT: if this game hits the turn cap (completed=False) the mechanic
    # is clearly unplayable — skip the full playtest immediately.
    replay_completed = True   # assume ok unless the recorded game says otherwise
    replay_data_dict = None
    try:
        mechanic_fn = load_mechanic_fn(mechanic.get("python_code", ""))
        replay = run_single_game_recorded(mechanic_fn=mechanic_fn, game_class=game_class)
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
    except Exception:
        pass  # Replay is nice-to-have, don't crash the pipeline

    # Fast-fail: if the recorded game never finished, the mechanic is unplayable.
    # No need to burn through 60 + 40 MCTS games to confirm what we already know.
    if not replay_completed:
        scores = {
            "playability": 0.0,
            "balance_gap": 1.0,
            "depth":       0.0,
            "aggregate":   0.0,
        }
        emitter.emit("playtest_start", {
            "mechanic_name": mechanic.get("mechanic_name", "unknown"),
        })
        emitter.emit("playtest_result", {"scores": scores})
        decision, feedback = verify(mechanic, scores, already_revised)
        if decision == REVISE:
            mechanic["_revision_feedback"] = feedback
        return decision, scores, None  # unplayable mechanics never reach the library

    # Playtest (use_signal=False since we run in a background thread)
    emitter.emit("playtest_start", {
        "mechanic_name": mechanic.get("mechanic_name", "unknown"),
    })

    with _suppress_stdout():
        scores = playtest(mechanic, game_class=game_class, use_signal=False)

    emitter.emit("playtest_result", {"scores": scores})

    # Verify
    with _suppress_stdout():
        decision, feedback = verify(mechanic, scores, already_revised)

    if decision == REVISE:
        mechanic["_revision_feedback"] = feedback

    return decision, scores, replay_data_dict
