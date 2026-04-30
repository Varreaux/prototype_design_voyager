"""
app.py
======
DesignVoyager Web Dashboard

FastAPI application with a WebSocket endpoint that streams
pipeline events to the browser in real time.

Run with:
    cd prototype_design_voyager
    uvicorn web.app:app --reload --port 8000

Then open http://localhost:8000
"""

import asyncio
import json
import queue
import sys
import threading
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="DesignVoyager Dashboard")

# Serve static files (HTML, CSS, JS)
_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/")
async def root():
    """Serve the dashboard page."""
    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.get("/phone")
async def phone_view():
    """
    Serve the phone-friendly Play vs AI page. Reached by a phone scanning
    the QR code shown in the dashboard's "Phone Mode" modal.
    """
    return FileResponse(os.path.join(_static_dir, "phone.html"))


def _get_local_ip() -> str:
    """
    Best-effort detection of this machine's LAN IP address.

    Opens a UDP socket toward 8.8.8.8 (no packets actually sent) and reads
    whichever local IP the kernel bound to it. This is the standard trick
    for getting the "outbound interface" IP without depending on hostname
    resolution. Falls back to 127.0.0.1 if the network is fully offline.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _get_ngrok_public_url() -> str:
    """
    Probe the local ngrok inspector (default http://localhost:4040) to see
    if a tunnel is currently running and, if so, return its public HTTPS URL.

    Returns the empty string when ngrok isn't running or doesn't have a
    tunnel for this server. The HTTP timeout is intentionally short so a
    missing ngrok process doesn't slow down the dashboard.
    """
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(
            "http://localhost:4040/api/tunnels", timeout=0.4,
        ) as resp:
            data = _json.loads(resp.read())
        tunnels = data.get("tunnels") or []
        # Prefer an HTTPS tunnel if multiple exist (ngrok usually exposes both).
        for t in tunnels:
            url = t.get("public_url", "")
            if url.startswith("https://"):
                return url
        if tunnels:
            return tunnels[0].get("public_url", "")
    except Exception:
        pass
    return ""


@app.get("/api/phone/info")
async def phone_info():
    """
    Return the URL the dashboard should put into the phone QR code.

    Priority:
      1. If an ngrok tunnel is running locally, return its public URL —
         this works on any network, including ones with client isolation
         (cafe / classroom WiFi).
      2. Otherwise fall back to the laptop's LAN IP at port 8000, which
         requires both devices to be on the same WiFi *and* uvicorn to be
         bound to 0.0.0.0.
    """
    ngrok_url = _get_ngrok_public_url()
    if ngrok_url:
        return JSONResponse(content={
            "host":   ngrok_url.split("://", 1)[-1].split("/")[0],
            "port":   None,
            "url":    f"{ngrok_url.rstrip('/')}/phone",
            "source": "ngrok",
        })

    ip = _get_local_ip()
    port = 8000   # matches the README's default uvicorn port
    return JSONResponse(content={
        "host":   ip,
        "port":   port,
        "url":    f"http://{ip}:{port}/phone",
        "source": "lan",
    })


@app.get("/api/library-cards")
async def get_library_cards():
    """
    Return all accepted mechanic cards saved by previous runs.
    Each card contains mechanic name, description, scores, and replay data
    sufficient to render the library browser and nano tutorial animation.
    """
    cards_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "library_cards.json",
    )
    try:
        with open(cards_file, "r") as f:
            cards = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        cards = []
    return JSONResponse(content=cards)


# Per-game library files. Mirrors GAME_REGISTRY in main.py / pipeline_runner.py.
_GAME_FILES = {
    "board": ("library.json",      "discarded_board.json"),
    "card":  ("library_card.json", "discarded_card.json"),
}


@app.post("/api/reset-library")
async def reset_library(payload: dict):
    """
    Delete the saved mechanic library and discarded-names file for the
    requested game, and remove that game's cards from library_cards.json
    (which holds cards for both games and is filtered rather than wiped).

    Body: {"game_name": "board" | "card"}
    """
    game_name = (payload or {}).get("game_name", "")
    if game_name not in _GAME_FILES:
        return JSONResponse(status_code=400, content={
            "ok":    False,
            "error": f"Unknown game_name {game_name!r}. Expected 'board' or 'card'.",
        })

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    library_file, discarded_file = _GAME_FILES[game_name]
    deleted = []

    for fname in (library_file, discarded_file):
        path = os.path.join(project_root, fname)
        if os.path.exists(path):
            try:
                os.remove(path)
                deleted.append(fname)
            except OSError as e:
                return JSONResponse(status_code=500, content={
                    "ok":    False,
                    "error": f"Failed to remove {fname}: {e}",
                })

    # library_cards.json holds cards for both games. Filter rather than wipe.
    cards_path    = os.path.join(project_root, "library_cards.json")
    cards_removed = 0
    if os.path.exists(cards_path):
        try:
            with open(cards_path, "r") as f:
                cards = json.load(f)
        except (json.JSONDecodeError, OSError):
            cards = []
        kept          = [c for c in cards if c.get("game_type") != game_name]
        cards_removed = len(cards) - len(kept)
        if cards_removed > 0:
            try:
                if kept:
                    with open(cards_path, "w") as f:
                        json.dump(kept, f, indent=2)
                else:
                    os.remove(cards_path)
                    deleted.append("library_cards.json")
            except OSError as e:
                return JSONResponse(status_code=500, content={
                    "ok":    False,
                    "error": f"Failed to update library_cards.json: {e}",
                })

    return JSONResponse(content={
        "ok":            True,
        "game_name":     game_name,
        "deleted":       deleted,
        "cards_removed": cards_removed,
    })


@app.get("/api/aivai/loadout")
async def get_aivai_loadout():
    """
    Return the top 2 card mechanics by aggregate score (descending,
    earliest-iteration tiebreak). Used by the AI vs AI tab to show the
    loadout strip without waiting for a full match to run.
    """
    # Make sure project root is on sys.path for the imports inside aivai_match.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from web.aivai_match import load_top_mechanics
    loadout = load_top_mechanics()
    return JSONResponse(content=[
        {
            "name":        m["name"],
            "description": m["description"],
            "aggregate":   m["aggregate"],
        }
        for m in loadout
    ])


@app.get("/api/pair-eval/stream")
async def get_pair_eval_stream(top_n: int = 10, games: int = 30, sims: int = 50,
                                agent_type: str = "mcts", depth: int = 4):
    """
    Server-Sent Events stream of the pair-lab evaluation.

    Each event is a JSON object on its own `data:` line, conforming to the
    SSE spec the browser's EventSource API consumes natively. The frontend
    opens this with `new EventSource('/api/pair-eval/stream?...')`.

    Query params:
        top_n      : top N mechanics from the library to use (default 10)
        games      : games per combo (default 30)
        sims       : MCTS simulations per move when agent_type='mcts' (default 50)
        agent_type : 'mcts' or 'minimax' (default 'mcts')
        depth      : alpha-beta depth when agent_type='minimax' (default 4)
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from web.pair_eval import iter_pair_eval
    from fastapi.responses import StreamingResponse

    def _event_payload(ev_type: str, data: dict) -> bytes:
        return f"event: {ev_type}\ndata: {json.dumps(data)}\n\n".encode()

    async def event_generator():
        # Run the (CPU-bound) eval in a worker thread so we don't block the
        # event loop. We pump events out one at a time via a background task.
        loop = asyncio.get_event_loop()
        q: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()

        def producer():
            try:
                for ev in iter_pair_eval(top_n=top_n, games_per_combo=games,
                                          simulations=sims,
                                          agent_type=agent_type, depth=depth):
                    asyncio.run_coroutine_threadsafe(q.put(ev), loop)
            except Exception as e:
                asyncio.run_coroutine_threadsafe(
                    q.put({"type": "error",
                           "data": {"message": f"{type(e).__name__}: {e}"}}),
                    loop,
                )
            finally:
                asyncio.run_coroutine_threadsafe(q.put(SENTINEL), loop)

        thread = threading.Thread(target=producer, daemon=True)
        thread.start()

        while True:
            ev = await q.get()
            if ev is SENTINEL:
                break
            yield _event_payload(ev["type"], ev["data"])

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/play/new")
async def post_play_new(payload: dict = None):
    """
    Start a new human-vs-AI card game session. Player 1 is the human,
    Player 2 is an MCTS or minimax agent depending on the agent_type.

    Body (optional):
      {
        "agent_type":     "mcts" | "minimax",   # default "mcts"
        "simulations":    int,                  # MCTS sims per move, default 200
        "depth":          int,                  # minimax search depth, default 8
        "mechanic_names": [str, ...]            # override default top-2 loadout
      }
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    payload = payload or {}
    agent_type = payload.get("agent_type", "mcts")
    sims       = int(payload.get("simulations", 200))
    depth      = int(payload.get("depth", 8))
    names      = payload.get("mechanic_names") or []

    from web.play_session import start_session
    # Wrap in to_thread so a slow first minimax search doesn't block the event loop.
    result = await asyncio.to_thread(
        start_session,
        sims, names if names else None, agent_type, depth,
    )
    return JSONResponse(content=result)


@app.post("/api/play/move")
async def post_play_move(payload: dict = None):
    """
    Submit the human's card index for the active session. The server applies
    the move, then runs any AI turns that follow until it's the human's turn
    again (or the game ends), and returns the full sequence of move events.

    Body: {"card_index": int}   # index into current player 1 hand, or -1 to pass
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    payload = payload or {}
    card_index = payload.get("card_index")
    if card_index is None:
        return JSONResponse(status_code=400, content={
            "error": "Missing card_index in request body."
        })

    from web.play_session import submit_human_move
    result = await asyncio.to_thread(submit_human_move, int(card_index))
    if "error" in result and "events" not in result:
        return JSONResponse(status_code=400, content=result)
    return JSONResponse(content=result)


@app.get("/api/play/status")
async def get_play_status():
    """Return the current session state (used by the frontend on tab open)."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from web.play_session import get_session_status
    return JSONResponse(content=get_session_status())


@app.post("/api/aivai/match")
async def post_aivai_match(payload: dict = None):
    """
    Run one AI vs AI card match and return the full per-move trace so the
    frontend can animate it locally.

    Body (optional):
      {
        "simulations":     int,      # default 200
        "mechanic_names": [str, ...] # if present, override the default top-2-by-aggregate
                                       loadout with these specific mechanics, in order.
      }
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    payload = payload or {}
    sims  = int(payload.get("simulations", 200))
    names = payload.get("mechanic_names") or []

    from web.aivai_match import run_match, load_mechanics_by_name
    loadout = load_mechanics_by_name(names) if names else None
    # Run the (CPU-bound) match in a worker thread so we don't block the loop.
    result = await asyncio.to_thread(run_match, loadout, sims)
    return JSONResponse(content=result)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    Single WebSocket connection per run.

    Protocol:
      Client sends: {"type": "start_run", "data": {"game_name", "iterations", "top_k"}}
      Server sends: stream of {"type": "...", "data": {...}} events
      Client sends: {"type": "stop_run"} to cancel early
    """
    await ws.accept()

    try:
        while True:
            # Wait for the client to send a start_run message
            raw = await ws.receive_text()
            msg = json.loads(raw)

            if msg.get("type") != "start_run":
                await ws.send_json({"type": "error", "data": {
                    "message": f"Expected start_run, got {msg.get('type')}"
                }})
                continue

            data       = msg.get("data", {})
            game_name  = data.get("game_name", "board")
            iterations = int(data.get("iterations", 3))
            top_k      = int(data.get("top_k", 3))

            # Event queue: pipeline thread writes, this coroutine reads
            event_queue = queue.Queue()
            stop_event  = threading.Event()

            # Import here to avoid circular imports at module load
            from web.pipeline_runner import EventEmitter, run_web_pipeline

            emitter = EventEmitter(event_queue)

            # Run the pipeline in a background thread
            pipeline_thread = threading.Thread(
                target=_run_pipeline_thread,
                args=(emitter, game_name, iterations, top_k, stop_event),
                daemon=True,
            )
            pipeline_thread.start()

            # Stream events to the client until the pipeline finishes
            try:
                await _stream_events(ws, event_queue, pipeline_thread, stop_event)
            except WebSocketDisconnect:
                stop_event.set()
                return

    except WebSocketDisconnect:
        pass


def _run_pipeline_thread(emitter, game_name, iterations, top_k, stop_event):
    """
    Wrapper that runs the pipeline and catches any unhandled exceptions,
    sending them as error events so the browser knows what happened.
    """
    # Change to the project root so library.json paths resolve correctly
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)

    from web.pipeline_runner import run_web_pipeline
    try:
        run_web_pipeline(emitter, game_name, iterations, top_k, stop_event)
    except Exception as e:
        emitter.emit("error", {"message": f"Pipeline crashed: {e}"})


async def _stream_events(ws, event_queue, pipeline_thread, stop_event):
    """
    Pull events from the queue and send them over the WebSocket.
    Also listens for stop_run messages from the client.
    """
    while True:
        # Check for incoming messages (stop_run) without blocking
        try:
            raw = await asyncio.wait_for(
                _receive_or_none(ws),
                timeout=0.05,
            )
            if raw is not None:
                msg = json.loads(raw)
                if msg.get("type") == "stop_run":
                    stop_event.set()
                    await ws.send_json({"type": "error", "data": {
                        "message": "Run stopped by user."
                    }})
        except asyncio.TimeoutError:
            pass

        # Drain all available events from the queue
        events_sent = False
        while True:
            try:
                event = event_queue.get_nowait()
                await ws.send_json(event)
                events_sent = True

                # If this was the final event, we're done
                if event.get("type") in ("run_complete", "error"):
                    return
            except queue.Empty:
                break

        # If the pipeline thread has died and the queue is empty, stop
        if not pipeline_thread.is_alive() and event_queue.empty():
            return

        # Small sleep to avoid busy-waiting
        if not events_sent:
            await asyncio.sleep(0.1)


async def _receive_or_none(ws):
    """Try to receive a WebSocket message; returns None if nothing available."""
    return await ws.receive_text()
