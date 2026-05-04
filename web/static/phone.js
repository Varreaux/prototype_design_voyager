/**
 * DesignVoyager phone view
 *
 * Minimal Play-vs-AI screen optimised for portrait phones. Uses the same
 * /api/play/* endpoints the desktop dashboard uses, so the backend is
 * unchanged. The phone is always Player 1; AI is always Player 2.
 */

// ── DOM refs ────────────────────────────────────────────────────────────────

const myHand        = document.getElementById('my-hand');
const myScore       = document.getElementById('my-score');
const myPlayed      = document.getElementById('my-played');
const myTurnBadge   = document.getElementById('my-turn-badge');
const oppScore      = document.getElementById('opp-score');
const oppHandCount  = document.getElementById('opp-hand-count');
const oppPlayed     = document.getElementById('opp-played');
const oppTurnBadge  = document.getElementById('opp-turn-badge');
const banner        = document.getElementById('banner');
const newGameBtn    = document.getElementById('new-game-btn');
const statusEl      = document.getElementById('status');
const resultEl      = document.getElementById('result');


// ── State ───────────────────────────────────────────────────────────────────

let state         = null;
let legalMoves    = [];
let busy          = false;
let finished      = false;
let loadout       = [];   // active mechanics, in display order
let handTriggers  = [];   // hand_triggers[i] = list of mechanic names that
                          //   would fire if the player taps card i

// URL query params, set by the dashboard's Phone Mode QR generator. These
// let the laptop operator control loadout AND difficulty centrally without
// adding extra UI to the phone view.
//
//   ?mechs=name1,name2   — mechanic loadout names
//   ?ai=mcts|minimax     — agent type (default mcts)
//   ?sims=N              — MCTS sims when ai=mcts (default 200)
//   ?depth=N             — minimax depth when ai=minimax (default 8)
//
// Human vs Human mode (when both ?session= and ?role= are present, the
// AI params above are ignored and the phone joins a live HvH session via
// WebSocket instead of /api/play/*):
//   ?session=ID          — HvH session id from /api/hvh/new
//   ?role=julian|tim     — which seat this phone occupies
const PHONE_PARAMS = new URLSearchParams(window.location.search);

const HVH_SESSION = PHONE_PARAMS.get('session') || '';
const HVH_ROLE    = (PHONE_PARAMS.get('role') || '').toLowerCase();
const IS_HVH      = !!(HVH_SESSION && (HVH_ROLE === 'julian' || HVH_ROLE === 'tim'));
// In HvH the local player is whichever seat the QR assigned. Julian = P1,
// Tim = P2. The phone UI mirrors that — the player's hand is "my" hand and
// the other seat is "opponent", regardless of which player number that is.
const HVH_MY_PLAYER  = (HVH_ROLE === 'julian') ? 1 : 2;
const HVH_OPP_PLAYER = (HVH_MY_PLAYER === 1) ? 2 : 1;
const HVH_OPP_NAME   = (HVH_ROLE === 'julian') ? 'Tim' : 'Julian';
const HVH_MY_NAME    = (HVH_ROLE === 'julian') ? 'Julian' : 'Tim';

// Which player number this phone *is*. In AI mode the human is always P1.
// In HvH it follows the role assigned by the QR.
const MY_PLAYER  = IS_HVH ? HVH_MY_PLAYER  : 1;
const OPP_PLAYER = IS_HVH ? HVH_OPP_PLAYER : 2;

const PHONE_LOADOUT_NAMES = (() => {
    const raw = PHONE_PARAMS.get('mechs');
    return raw ? raw.split(',').map(s => s.trim()).filter(Boolean) : [];
})();

const PHONE_AI_TYPE = (() => {
    const v = (PHONE_PARAMS.get('ai') || 'mcts').toLowerCase();
    return (v === 'minimax') ? 'minimax' : 'mcts';
})();

const PHONE_AI_SIMS = (() => {
    const v = parseInt(PHONE_PARAMS.get('sims') || '200', 10);
    return Number.isFinite(v) && v > 0 ? v : 200;
})();

const PHONE_AI_DEPTH = (() => {
    const v = parseInt(PHONE_PARAMS.get('depth') || '8', 10);
    return Number.isFinite(v) && v >= 2 ? v : 8;
})();


// ── New game ────────────────────────────────────────────────────────────────

async function startNewGame() {
    busy = true;
    newGameBtn.disabled = true;
    statusEl.textContent = 'Starting...';
    finished = false;
    resultEl.classList.add('hidden');
    clearBanner();
    myPlayed.textContent = '—';
    myPlayed.classList.add('dim');
    myPlayed.classList.remove('recent');
    oppPlayed.textContent = '—';
    oppPlayed.classList.add('dim');
    oppPlayed.classList.remove('recent');

    try {
        const resp = await fetch('/api/play/new', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({
                // All three of these come from the QR's query string so the
                // dashboard operator picks the difficulty centrally.
                agent_type:     PHONE_AI_TYPE,
                simulations:    PHONE_AI_SIMS,
                depth:          PHONE_AI_DEPTH,
                mechanic_names: PHONE_LOADOUT_NAMES,
            }),
        });
        if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
        const data = await resp.json();
        adoptSession(data);
        statusEl.textContent = 'Your turn';
    } catch (e) {
        statusEl.textContent = `Failed: ${e}`;
    } finally {
        busy = false;
        newGameBtn.disabled = false;
        // Re-render with busy=false so the dealt cards become tappable.
        // Without this, cards rendered inside adoptSession() above stay
        // permanently disabled (busy was still true when they were drawn).
        renderState();
    }
}

function adoptSession(data) {
    state         = data.state;
    legalMoves    = data.legal_moves || [];
    finished      = !!data.finished;
    loadout       = data.loadout || [];
    handTriggers  = data.hand_triggers || [];
    renderLoadout(loadout);
    renderState();
}

function renderLoadout(loadout) {
    const container = document.getElementById('phone-loadout');
    const cardsEl   = document.getElementById('phone-loadout-cards');
    if (!container || !cardsEl) return;

    if (!loadout || loadout.length === 0) {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    cardsEl.innerHTML = '';
    loadout.forEach((m, i) => {
        const card = document.createElement('div');
        card.className = `phone-mech-card mech-${i + 1}`;
        card.innerHTML =
            '<div class="phone-mech-card-head">' +
                '<span class="phone-mech-card-dot"></span>' +
                `<span class="phone-mech-card-name">${escapeText(m.name)}</span>` +
            '</div>' +
            `<div class="phone-mech-card-desc">${escapeText(m.description || 'No description.')}</div>`;
        cardsEl.appendChild(card);
    });
}


// ── Render ──────────────────────────────────────────────────────────────────

function renderState() {
    if (!state) return;

    const myScoreVal  = state.scores[MY_PLAYER]  ?? state.scores[String(MY_PLAYER)]  ?? 0;
    const oppScoreVal = state.scores[OPP_PLAYER] ?? state.scores[String(OPP_PLAYER)] ?? 0;
    const myHandArr   = state.hands[MY_PLAYER]   || state.hands[String(MY_PLAYER)]   || [];
    const oppHandArr  = state.hands[OPP_PLAYER]  || state.hands[String(OPP_PLAYER)]  || [];

    myScore.textContent = myScoreVal;
    oppScore.textContent = oppScoreVal;
    oppHandCount.textContent = oppHandArr.length;

    // Build clickable hand for the local player, with pulse highlighting on
    // cards that would trigger a mechanic if played right now.
    const interactive = !busy && !finished && state.current_player === MY_PLAYER;
    myHand.innerHTML = '';
    myHandArr.forEach((val, idx) => {
        const card = document.createElement('button');
        card.className = 'card';
        card.textContent = val;

        if (!interactive) {
            card.classList.add('disabled');
        } else {
            card.addEventListener('click', () => playCard(idx));

            // Translate the mechanic-name list at hand_triggers[idx] into
            // mechanic indices (1 or 2) by looking them up in the loadout,
            // then pick the right pulse animation.
            const triggerNames = handTriggers[idx] || [];
            const mechSlots = triggerNames
                .map(n => loadout.findIndex(m => m && m.name === n) + 1)
                .filter(slot => slot >= 1 && slot <= 2);
            const uniqueSlots = [...new Set(mechSlots)];
            if (uniqueSlots.length === 1) {
                card.classList.add(`pulse-mech-${uniqueSlots[0]}`);
            } else if (uniqueSlots.length >= 2) {
                card.classList.add('pulse-both');
            }
        }

        myHand.appendChild(card);
    });

    // Turn badges
    const myTurn  = !finished && state.current_player === MY_PLAYER;
    const oppTurn = !finished && state.current_player === OPP_PLAYER;
    myTurnBadge.textContent  = finished ? 'Game over' : (myTurn ? 'Your turn' : 'Waiting');
    oppTurnBadge.textContent = finished ? '—'         : (oppTurn ? 'Their turn' : 'Waiting');
    myTurnBadge.classList.toggle('active', myTurn);
    oppTurnBadge.classList.toggle('active', oppTurn);
}


// ── Play a card ─────────────────────────────────────────────────────────────

async function playCard(cardIndex) {
    if (busy || finished) return;
    if (!state || state.current_player !== MY_PLAYER) return;
    if (!legalMoves.includes(cardIndex)) return;

    // HvH path: send the move over the WebSocket and wait for the server to
    // broadcast back the resulting events + state. The optimistic card-fly
    // is the same — the server owns truth and a state push will reconcile.
    if (IS_HVH) {
        busy = true;
        statusEl.textContent = `Waiting for ${HVH_OPP_NAME}...`;
        const beforeState = state;
        const card = (beforeState.hands[MY_PLAYER]
                   || beforeState.hands[String(MY_PLAYER)] || [])[cardIndex];
        renderLocalRawMove(beforeState, cardIndex, card);
        try {
            hvhSocket.send(JSON.stringify({ type: 'play', card_index: cardIndex }));
        } catch (e) {
            statusEl.textContent = `Send failed: ${e}`;
            busy = false;
            renderState();
        }
        return;
    }

    busy = true;
    statusEl.textContent = aiThinkingLabel();

    // Optimistic local card-fly: pop the card immediately so the user gets
    // instant feedback. The server still owns truth (mechanics + AI move).
    const beforeState = state;
    const card = beforeState.hands[1] ? beforeState.hands[1][cardIndex]
                                      : beforeState.hands['1'][cardIndex];
    renderLocalRawMove(beforeState, cardIndex, card);

    try {
        const resp = await fetch('/api/play/move', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ card_index: cardIndex }),
        });
        const data = await resp.json();
        if (!resp.ok || (data.error && !data.events)) {
            statusEl.textContent = `Error: ${data.error || resp.status}`;
            busy = false;
            renderState();
            return;
        }
        animateEvents(data.events || [], /*skipFirstRaw=*/true, () => {
            state         = data.state;
            legalMoves    = data.legal_moves || [];
            finished      = !!data.finished;
            handTriggers  = data.hand_triggers || [];
            busy          = false;
            if (finished) {
                showResult();
                statusEl.textContent = 'Game over';
            } else if (state.current_player === 1) {
                statusEl.textContent = 'Your turn';
            } else {
                statusEl.textContent = 'AI thinking...';
            }
            renderState();
        });
    } catch (e) {
        statusEl.textContent = `Move failed: ${e}`;
        busy = false;
        renderState();
    }
}


// ── Local optimistic render ─────────────────────────────────────────────────

function renderLocalRawMove(beforeState, cardIndex, card) {
    // Pop the card visually so the user sees it leave the hand right away.
    const handBefore = beforeState.hands[MY_PLAYER]
                    || beforeState.hands[String(MY_PLAYER)] || [];
    const handAfter  = handBefore.slice();
    handAfter.splice(cardIndex, 1);

    // Re-render the local hand without the played card, all disabled.
    myHand.innerHTML = '';
    handAfter.forEach(val => {
        const c = document.createElement('button');
        c.className = 'card disabled';
        c.textContent = val;
        myHand.appendChild(c);
    });

    // Show the played card in the "Just played" slot.
    myPlayed.textContent = card;
    myPlayed.classList.remove('dim');
    myPlayed.classList.add('recent');

    // Bump the score display optimistically.
    const myScoreVal = beforeState.scores[MY_PLAYER]
                    ?? beforeState.scores[String(MY_PLAYER)] ?? 0;
    myScore.textContent = myScoreVal + card;

    // Clear any prior banner and clear opponent's just-played slot.
    oppPlayed.textContent = '—';
    oppPlayed.classList.add('dim');
    oppPlayed.classList.remove('recent');
    clearBanner();
}


// ── Event animation ─────────────────────────────────────────────────────────

function animateEvents(events, skipFirstRaw, onDone) {
    if (!events || events.length === 0) {
        onDone();
        return;
    }
    const STEP_MS = 1100;
    let i = 0;

    const renderOne = () => {
        const ev = events[i];
        renderEvent(ev, /*skipRaw=*/skipFirstRaw && i === 0);
        i++;
        if (i < events.length) {
            setTimeout(renderOne, STEP_MS);
        } else {
            setTimeout(onDone, 300);
        }
    };

    if (skipFirstRaw) {
        // Give the optimistic local fly a moment to land before we splash a
        // mechanic banner on top of it.
        setTimeout(renderOne, 400);
    } else {
        renderOne();
    }
}

function renderEvent(ev, skipRaw) {
    const before = ev.before_move;
    const afterRaw = ev.after_raw_move;
    const fired = (ev.mechanics || []).filter(m => m.fired);

    if (!skipRaw) {
        // Update the "just played" slot for whichever player moved.
        if (ev.player === MY_PLAYER) {
            myPlayed.textContent = ev.card_played != null ? ev.card_played : '—';
            myPlayed.classList.toggle('dim', ev.card_played == null);
            myPlayed.classList.toggle('recent', ev.card_played != null);
            oppPlayed.classList.remove('recent');
            oppPlayed.classList.add('dim');
            oppPlayed.textContent = '—';
        } else {
            oppPlayed.textContent = ev.card_played != null ? ev.card_played : '—';
            oppPlayed.classList.toggle('dim', ev.card_played == null);
            oppPlayed.classList.toggle('recent', ev.card_played != null);
            myPlayed.classList.remove('recent');
            myPlayed.classList.add('dim');
            myPlayed.textContent = '—';
        }

        // Render hand counts / scores for the "raw" state.
        renderScoresAndCounts(afterRaw, before);
    }

    // Mechanic phase
    if (fired.length === 0) {
        clearBanner();
    } else {
        showBanner(fired);
        const finalState = ev.after || ev.mechanics[ev.mechanics.length - 1].after;
        renderScoresAndCounts(finalState, before);
    }
}

function renderScoresAndCounts(stateLike, prevState) {
    if (!stateLike) return;
    const myScoreVal  = stateLike.scores[MY_PLAYER]  ?? stateLike.scores[String(MY_PLAYER)]  ?? 0;
    const oppScoreVal = stateLike.scores[OPP_PLAYER] ?? stateLike.scores[String(OPP_PLAYER)] ?? 0;
    const prevMy  = prevState ? (prevState.scores[MY_PLAYER]  ?? prevState.scores[String(MY_PLAYER)]  ?? 0) : myScoreVal;
    const prevOpp = prevState ? (prevState.scores[OPP_PLAYER] ?? prevState.scores[String(OPP_PLAYER)] ?? 0) : oppScoreVal;

    flashScore(myScore, myScoreVal, prevMy);
    flashScore(oppScore, oppScoreVal, prevOpp);

    const oppHandArr = stateLike.hands[OPP_PLAYER] || stateLike.hands[String(OPP_PLAYER)] || [];
    oppHandCount.textContent = oppHandArr.length;
}

function flashScore(el, newVal, oldVal) {
    el.textContent = newVal;
    el.classList.remove('flash-up', 'flash-down');
    if (newVal > oldVal)      el.classList.add('flash-up');
    else if (newVal < oldVal) el.classList.add('flash-down');
}


// ── Mechanic banner ─────────────────────────────────────────────────────────

function showBanner(firedList) {
    banner.className = 'banner fired';

    // Pick the banner's accent color from whichever mechanic(s) fired.
    // Single-fire: the banner takes that mechanic's slot color (mech-1 or
    // mech-2). Multi-fire: 'mixed' (neutral border) so each per-name color
    // wins individually below.
    const slots = [...new Set(firedList.map(f =>
        loadout.findIndex(m => m && m.name === f.name) + 1
    ).filter(s => s >= 1 && s <= 2))];
    if (slots.length === 1) {
        banner.classList.add(`mech-${slots[0]}`);
    } else if (slots.length >= 2) {
        banner.classList.add('mixed');
    }

    banner.innerHTML = firedList.map((f, i) => {
        const slot = loadout.findIndex(m => m && m.name === f.name) + 1;
        const nameCls = (slot >= 1 && slot <= 2) ? `mech-${slot}` : '';
        const parts = [];
        const sc = f.score_changes || {};
        for (const p of ['1', '2']) {
            if (sc[p]) {
                const delta = sc[p].after - sc[p].before;
                if (sc[p].after === 0 && sc[p].before > 0) {
                    parts.push(`P${p} reset to 0 (was ${sc[p].before})`);
                } else if (delta > 0) {
                    parts.push(`P${p} +${delta} → ${sc[p].after}`);
                } else if (delta < 0) {
                    parts.push(`P${p} ${delta} → ${sc[p].after}`);
                }
            }
        }
        const hc = f.hand_changes || {};
        for (const p of ['1', '2']) {
            if (hc[p]) parts.push(`P${p} hand changed`);
        }
        if (f.extra_turn_changed) parts.push('Extra turn granted');
        const effect = parts.join(' · ') || 'Effect applied';
        return (i > 0 ? '<hr class="banner-divider">' : '') +
               `<div><div class="banner-mech-name ${nameCls}">${escapeText(f.name)}</div>${escapeText(effect)}</div>`;
    }).join('');
}

function clearBanner() {
    banner.className = 'banner';
    banner.innerHTML = 'No mechanic fired yet';
}


// ── Result ──────────────────────────────────────────────────────────────────

function showResult() {
    const myScoreVal  = state.scores[MY_PLAYER]  ?? state.scores[String(MY_PLAYER)]  ?? 0;
    const oppScoreVal = state.scores[OPP_PLAYER] ?? state.scores[String(OPP_PLAYER)] ?? 0;
    let title, cls;
    const myWon  = (myScoreVal  >= 45 && myScoreVal  > oppScoreVal);
    const oppWon = (oppScoreVal >= 45 && oppScoreVal > myScoreVal);
    const oppLabel = IS_HVH ? HVH_OPP_NAME : 'AI';

    if (myWon) {
        title = 'You win!';
        cls = '';
    } else if (oppWon) {
        title = `${oppLabel} wins`;
        cls = 'lost';
    } else if (myScoreVal > oppScoreVal) {
        title = 'You lead — no one reached 45';
        cls = '';
    } else if (oppScoreVal > myScoreVal) {
        title = `${oppLabel} leads — no one reached 45`;
        cls = 'lost';
    } else {
        title = 'Draw';
        cls = 'draw';
    }

    resultEl.className = `result ${cls}`;
    resultEl.innerHTML = `
        <div class="result-title">${title}</div>
        <div class="result-detail">Final scores  You ${myScoreVal}  ·  ${oppLabel} ${oppScoreVal}</div>
    `;
    resultEl.classList.remove('hidden');
    setTimeout(() => resultEl.scrollIntoView({ behavior: 'smooth', block: 'center' }), 100);
}


// ── Helpers ─────────────────────────────────────────────────────────────────

function escapeText(str) {
    const div = document.createElement('div');
    div.textContent = String(str || '');
    return div.innerHTML;
}

function aiThinkingLabel() {
    return PHONE_AI_TYPE === 'minimax'
        ? `AI thinking (minimax depth ${PHONE_AI_DEPTH})...`
        : `AI thinking (${PHONE_AI_SIMS} sims)...`;
}


// ── Human vs Human mode ─────────────────────────────────────────────────────

let hvhSocket = null;

function startHvhSession() {
    // Update labels so the player knows which seat they're in.
    document.querySelector('.opponent .player-label').textContent =
        `${HVH_OPP_NAME} (Player ${HVH_OPP_PLAYER})`;
    document.querySelector('.me .player-label').textContent =
        `You (${HVH_MY_NAME}, Player ${HVH_MY_PLAYER})`;
    document.title = `DesignVoyager — ${HVH_MY_NAME}`;
    // The laptop spectator owns "new game" in HvH; hide it on the phone so
    // one player can't surprise-restart on the other.
    newGameBtn.classList.add('hidden');

    statusEl.textContent = 'Connecting...';
    const proto = (window.location.protocol === 'https:') ? 'wss' : 'ws';
    const url = `${proto}://${window.location.host}/ws/hvh/${encodeURIComponent(HVH_SESSION)}`
              + `?role=${encodeURIComponent(HVH_ROLE)}`;
    hvhSocket = new WebSocket(url);

    hvhSocket.addEventListener('open', () => {
        statusEl.textContent = 'Waiting for game state...';
    });

    hvhSocket.addEventListener('message', (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }

        if (msg.type === 'state') {
            adoptHvhSnapshot(msg);
        } else if (msg.type === 'events') {
            // Replay the per-move animations the same way the AI flow does.
            // skipFirstRaw is true if this phone played the move (we already
            // showed the optimistic card-fly).
            const wePlayed = busy;
            animateEvents(msg.events || [], /*skipFirstRaw=*/wePlayed, () => {
                // The state push that follows will re-render and clear busy.
            });
        } else if (msg.type === 'error') {
            statusEl.textContent = `Error: ${msg.message}`;
            busy = false;
            renderState();
        }
    });

    hvhSocket.addEventListener('close', () => {
        statusEl.textContent = 'Disconnected. Refresh the page to reconnect.';
    });

    hvhSocket.addEventListener('error', () => {
        statusEl.textContent = 'Connection error.';
    });
}

function adoptHvhSnapshot(msg) {
    state         = msg.state;
    legalMoves    = msg.legal_moves || [];
    finished      = !!msg.finished;
    loadout       = msg.loadout || [];
    // Triggers are computed for state.current_player. The pulse-render path
    // in renderState only draws on cards when current_player === MY_PLAYER,
    // so the predictions only ever decorate the right player's hand.
    handTriggers  = msg.hand_triggers || [];
    busy          = false;
    renderLoadout(loadout);
    renderState();
    if (finished) {
        showResult();
        statusEl.textContent = 'Game over';
    } else if (state.current_player === MY_PLAYER) {
        statusEl.textContent = 'Your turn';
    } else {
        statusEl.textContent = `Waiting for ${HVH_OPP_NAME}...`;
    }
}


// ── Wire up ─────────────────────────────────────────────────────────────────

if (IS_HVH) {
    startHvhSession();
} else {
    newGameBtn.addEventListener('click', startNewGame);
    // Auto-start a fresh game so scanning the QR drops the user straight in.
    startNewGame();
}
