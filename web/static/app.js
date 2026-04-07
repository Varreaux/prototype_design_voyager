/**
 * DesignVoyager Dashboard
 * WebSocket client, event rendering, and game replay player.
 */

// ── DOM references ──────────────────────────────────────────────────────────

const logContent    = document.getElementById('log-content');
const startBtn      = document.getElementById('start-btn');
const stopBtn       = document.getElementById('stop-btn');
const gameSelect    = document.getElementById('game-select');
const iterInput     = document.getElementById('iterations-input');
const topkInput     = document.getElementById('topk-input');
const connDot       = document.getElementById('connection-dot');

// Mechanic info
const mechanicInfo  = document.getElementById('mechanic-info');
const mechanicName  = document.getElementById('mechanic-info-name');
const mechanicDesc  = document.getElementById('mechanic-info-desc');

// Tutorial panel
const tutorialEmptyState  = document.getElementById('tutorial-empty-state');
const tutorialContent     = document.getElementById('tutorial-content');
const tutorialMechLabel   = document.getElementById('tutorial-mech-label');
const tutorialPhaseLabel  = document.getElementById('tutorial-phase-label');
const tutorialGrid        = document.getElementById('tutorial-grid');
const tutorialCaption     = document.getElementById('tutorial-caption');
const tutorialNoTrigger   = document.getElementById('tutorial-no-trigger');

// Replay
const boardGrid     = document.getElementById('board-grid');
const cardDisplay   = document.getElementById('card-display');
const replayEmpty   = document.getElementById('replay-empty');
const replayControls= document.getElementById('replay-controls');
const replayTurn    = document.getElementById('replay-turn');
const replayWinner  = document.getElementById('replay-winner');
const replayPlay    = document.getElementById('replay-play');
const replayBack    = document.getElementById('replay-back');
const replayForward = document.getElementById('replay-forward');
const replaySpeed   = document.getElementById('replay-speed');


// ── WebSocket ───────────────────────────────────────────────────────────────

let ws = null;
let running = false;

function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        connDot.className = 'dot connected';
        connDot.title = 'Connected';
    };

    ws.onclose = () => {
        connDot.className = 'dot disconnected';
        connDot.title = 'Disconnected';
        running = false;
        updateButtons();
        // Auto-reconnect after 2 seconds
        setTimeout(connect, 2000);
    };

    ws.onerror = () => {
        ws.close();
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleEvent(msg.type, msg.data || {});
    };
}

connect();


// ── Controls ────────────────────────────────────────────────────────────────

startBtn.addEventListener('click', () => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    // Clear previous output
    logContent.innerHTML = '';
    replayPlayer.reset();
    tutorialPlayer.reset();

    running = true;
    updateButtons();

    ws.send(JSON.stringify({
        type: 'start_run',
        data: {
            game_name:  gameSelect.value,
            iterations: parseInt(iterInput.value, 10),
            top_k:      parseInt(topkInput.value, 10),
        }
    }));
});

stopBtn.addEventListener('click', () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'stop_run' }));
    }
    running = false;
    updateButtons();
});

function updateButtons() {
    startBtn.classList.toggle('hidden', running);
    stopBtn.classList.toggle('hidden', !running);
    gameSelect.disabled  = running;
    iterInput.disabled   = running;
    topkInput.disabled   = running;
}


// ── Event handler dispatch ──────────────────────────────────────────────────

function handleEvent(type, data) {
    switch (type) {
        case 'welcome':           renderWelcome(data); break;
        case 'iteration_start':   renderIterationStart(data); break;
        case 'retrieve':          renderRetrieve(data); break;
        case 'propose_start':     renderProposeStart(data); break;
        case 'propose_result':    renderProposeResult(data); break;
        case 'compile_result':    renderCompileResult(data); break;
        case 'playtest_start':    renderPlaytestStart(data); break;
        case 'playtest_result':   renderPlaytestResult(data); break;
        case 'replay_data':       replayPlayer.load(data); tutorialPlayer.load(data); break;
        case 'mechanic_accepted': libraryManager.addLive(data); break;
        case 'verify_result':     renderVerifyResult(data); break;
        case 'revision_start':    renderRevisionStart(data); break;
        case 'revision_result':   renderRevisionResult(data); break;
        case 'curriculum_advance':renderCurriculumAdvance(data); break;
        case 'run_complete':      renderRunComplete(data); break;
        case 'error':             renderError(data); break;
    }
    autoScroll();
}


// ── Render functions ────────────────────────────────────────────────────────

function renderWelcome(d) {
    const html = `
        <div class="welcome-banner">
            <h2>DesignVoyager</h2>
            <div class="info-grid">
                <span class="info-label">Game</span>
                <span>${d.game_name} (${d.game_class})</span>
                <span class="info-label">Iterations</span>
                <span>${d.iterations}</span>
                <span class="info-label">Context k</span>
                <span>${d.top_k}</span>
                <span class="info-label">Library</span>
                <span style="color:var(--cyan)">${d.library_size} mechanics</span>
                <span class="info-label">Curriculum</span>
                <span style="color:var(--yellow)">${d.curriculum_progress}</span>
            </div>
        </div>`;
    logContent.insertAdjacentHTML('beforeend', html);
}

function renderIterationStart(d) {
    const html = `
        <div class="iteration-header">
            Iteration ${d.iteration} of ${d.total}
            <span class="stage-tag">${d.stage_name}</span>
        </div>`;
    logContent.insertAdjacentHTML('beforeend', html);
}

function renderRetrieve(d) {
    const names = d.mechanic_names.length
        ? `<span class="mechanic-names">${d.mechanic_names.join(', ')}</span>`
        : 'No library context yet';
    const html = `<div class="context-line">Context from library: ${names}</div>`;
    logContent.insertAdjacentHTML('beforeend', html);
}

function renderProposeStart(d) {
    const html = `
        <div class="status-line" id="propose-spinner">
            <span class="spinner"></span>
            Gemini is designing a new mechanic (using ${d.context_count} as context)...
        </div>`;
    logContent.insertAdjacentHTML('beforeend', html);
}

function renderProposeResult(d) {
    // Remove spinner
    const spinner = document.getElementById('propose-spinner');
    if (spinner) spinner.remove();

    if (d.failed) {
        const html = `<div class="compile-line fail">Proposal failed.</div>`;
        logContent.insertAdjacentHTML('beforeend', html);
        return;
    }

    const codeId = 'code-' + Date.now();
    const html = `
        <div class="proposal-card">
            <span class="mech-name">${d.mechanic_name}</span>
            <span class="mech-type">${d.mechanic_type}</span>
            <div class="mech-desc">${d.description}</div>
            <span class="code-toggle" onclick="toggleCode('${codeId}')">Show code</span>
            <pre id="${codeId}">${escapeHtml(d.python_code)}</pre>
        </div>`;
    logContent.insertAdjacentHTML('beforeend', html);
}

function renderCompileResult(d) {
    if (d.passed) {
        logContent.insertAdjacentHTML('beforeend',
            `<div class="compile-line pass">Compile check &#10003; passed</div>`);
    } else {
        logContent.insertAdjacentHTML('beforeend',
            `<div class="compile-line fail">Compile check &#10007; failed</div>
             <div class="compile-error">${escapeHtml(d.error || '').slice(0, 120)}</div>`);
    }
}

function renderPlaytestStart(d) {
    logContent.insertAdjacentHTML('beforeend', `
        <div class="status-line" id="playtest-spinner">
            <span class="spinner"></span>
            Playtesting <strong>${d.mechanic_name}</strong>...
        </div>`);
}

function renderPlaytestResult(d) {
    const spinner = document.getElementById('playtest-spinner');
    if (spinner) spinner.remove();

    const s = d.scores;
    const balance = Math.round((1 - s.balance_gap) * 1000) / 1000;

    const playGate = s.playability >= 1.0
        ? `<div class="playability-gate pass">Playability gate &#10003; passed</div>`
        : `<div class="playability-gate fail">Playability gate &#10007; failed (${(s.playability * 100).toFixed(0)}%)</div>`;

    const html = `
        <div class="scores-section">
            ${playGate}
            ${scoreBar('Balance', balance)}
            ${scoreBar('Depth', s.depth)}
            <hr class="score-divider">
            ${scoreBar('Aggregate', s.aggregate)}
        </div>`;
    logContent.insertAdjacentHTML('beforeend', html);
}

function renderVerifyResult(d) {
    const decision = d.decision;
    let title, detail;

    if (decision === 'accept') {
        const agg = d.scores && d.scores.aggregate != null
            ? `aggregate score: ${d.scores.aggregate.toFixed(2)}`
            : '';
        title = '&#10003; ACCEPTED';
        detail = agg;
    } else if (decision === 'revise') {
        title = '&rarr; REVISING';
        detail = 'Sending feedback to Gemini for one revision attempt...';
    } else {
        title = '&#10007; DISCARDED';
        detail = d.feedback || 'Could not produce a working mechanic.';
    }

    logContent.insertAdjacentHTML('beforeend', `
        <div class="verdict-panel ${decision}">
            <div class="verdict-title">${title}</div>
            <div class="verdict-detail">${detail}</div>
        </div>`);
}

function renderRevisionStart(d) {
    logContent.insertAdjacentHTML('beforeend', `
        <div class="status-line" id="revision-spinner">
            <span class="spinner"></span>
            Gemini is revising <strong>${d.mechanic_name}</strong>...
        </div>`);
}

function renderRevisionResult(d) {
    const spinner = document.getElementById('revision-spinner');
    if (spinner) spinner.remove();

    if (d.failed) {
        logContent.insertAdjacentHTML('beforeend',
            `<div class="compile-line fail">Revision failed.</div>`);
        return;
    }

    const codeId = 'code-' + Date.now();
    logContent.insertAdjacentHTML('beforeend', `
        <div class="proposal-card">
            <span class="mech-name">${d.mechanic_name}</span>
            <span class="mech-type">${d.mechanic_type}</span>
            <div class="mech-desc">${d.description}</div>
            <span class="code-toggle" onclick="toggleCode('${codeId}')">Show code</span>
            <pre id="${codeId}">${escapeHtml(d.python_code)}</pre>
        </div>`);
}

function renderCurriculumAdvance(d) {
    logContent.insertAdjacentHTML('beforeend', `
        <div class="curriculum-advance">
            &#9733; Unlocked ${d.new_stage_name}! Gemini will now propose more complex mechanics.
        </div>`);
}

function renderRunComplete(d) {
    running = false;
    updateButtons();
    tutorialPlayer._stopLoop();

    const mechList = d.mechanic_names.length
        ? d.mechanic_names.join(', ')
        : 'empty';

    logContent.insertAdjacentHTML('beforeend', `
        <div class="summary-panel">
            <h3>Run Complete</h3>
            <div class="summary-grid">
                <span class="label">Accepted</span>
                <span class="val-green">${d.accepted_count}</span>
                <span class="label">Discarded</span>
                <span class="val-red">${d.discarded_count}</span>
                <span class="label">Library</span>
                <span class="val-cyan">${d.library_size} mechanics</span>
                <span class="label">Mechanics</span>
                <span class="dim">${mechList}</span>
            </div>
        </div>`);
}

function renderError(d) {
    running = false;
    updateButtons();
    logContent.insertAdjacentHTML('beforeend', `
        <div class="verdict-panel discard">
            <div class="verdict-title">Error</div>
            <div class="verdict-detail">${escapeHtml(d.message || 'Unknown error')}</div>
        </div>`);
}


// ── Score bar helper ────────────────────────────────────────────────────────

function scoreBar(label, value) {
    const pct = Math.max(0, Math.min(100, value * 100));
    const color = value >= 0.75 ? 'green' : (value >= 0.5 ? 'yellow' : 'red');
    return `
        <div class="score-row">
            <span class="score-label">${label}</span>
            <div class="score-bar-container">
                <div class="score-bar-fill ${color}" style="width:${pct}%"></div>
            </div>
            <span class="score-value">${value.toFixed(2)}</span>
        </div>`;
}


// ── Helpers ──────────────────────────────────────────────────────────────────

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function toggleCode(id) {
    const pre = document.getElementById(id);
    if (pre) pre.classList.toggle('open');
}

function autoScroll() {
    const log = document.getElementById('pipeline-log');
    log.scrollTop = log.scrollHeight;
}


// ── Mechanic Trigger Detection ──────────────────────────────────────────────
//
// Shared by both the live tutorial panel and the library card animations.
// Scans a replay move list for the first turn where the mechanic visibly fired.
// Returns a trigger object or null if no effect was detectable.
//
// Three passes in priority order:
//   1. Board cell changes   (state_before_mechanics vs state_after)
//   2. Extra turn granted   (same player appears twice in a row)
//   3. custom_state changed (consecutive turns differ on custom_state field)

function detectMechanicTrigger(moves) {
    if (!moves || moves.length === 0) return null;

    // Pass 1: cells changed by mechanic
    for (const move of moves) {
        if (!move.state_before_mechanics) continue;
        const before = move.state_before_mechanics.board;
        const after  = move.state_after.board;
        if (!before || !after) continue;
        const changes = [];
        for (let r = 0; r < before.length; r++) {
            for (let c = 0; c < before[r].length; c++) {
                if (before[r][c] !== after[r][c]) changes.push(`${r},${c}`);
            }
        }
        if (changes.length > 0) {
            return { type: 'board', before, after,
                     changes: new Set(changes), move: move.move };
        }
    }

    // Pass 2: extra turn — same player moves twice in a row
    for (let i = 0; i < moves.length - 1; i++) {
        if (moves[i].player === moves[i + 1].player) {
            return {
                type:      'extra_turn',
                before:    moves[i].state_after.board,
                after:     moves[i + 1].state_after.board,
                changes:   new Set(),
                move:      moves[i].move,
                bonusMove: moves[i + 1].move,
            };
        }
    }

    // Pass 3: custom_state changed between consecutive turns
    for (let i = 1; i < moves.length; i++) {
        const prev = moves[i - 1].state_after;
        const curr = moves[i].state_after;
        if (JSON.stringify(prev.custom_state) !== JSON.stringify(curr.custom_state)) {
            return {
                type:    'custom_state',
                before:  prev.board,
                after:   curr.board,
                changes: new Set(),
                move:    moves[i].move,
            };
        }
    }

    return null;
}


// ── Game Replay Player ──────────────────────────────────────────────────────

const replayPlayer = {
    data: null,
    currentStep: -1,   // -1 = initial state, 0..N-1 = after each move
    interval: null,
    playing: false,

    reset() {
        this.stop();
        this.data = null;
        this.currentStep = -1;
        boardGrid.classList.add('hidden');
        cardDisplay.classList.add('hidden');
        replayEmpty.classList.remove('hidden');
        replayControls.classList.add('hidden');
        mechanicInfo.classList.add('hidden');
    },

    load(d) {
        this.stop();
        this.data = d;
        this.currentStep = -1;

        replayEmpty.classList.add('hidden');
        replayControls.classList.remove('hidden');

        // Show mechanic info if present
        if (d.mechanic_name) {
            mechanicName.textContent = d.mechanic_name;
            mechanicDesc.textContent = d.mechanic_description || '';
            mechanicInfo.classList.remove('hidden');
        } else {
            mechanicInfo.classList.add('hidden');
        }

        if (d.game_type === 'board') {
            boardGrid.classList.remove('hidden');
            cardDisplay.classList.add('hidden');
            this._initBoardGrid();
        } else {
            cardDisplay.classList.remove('hidden');
            boardGrid.classList.add('hidden');
        }

        this._renderCurrentState();
        this._updateInfo();

        // Auto-play
        this.play();
    },

    play() {
        if (!this.data || this.playing) return;
        this.playing = true;
        replayPlay.innerHTML = '&#9646;&#9646;';  // pause icon

        const speed = parseInt(replaySpeed.value, 10);
        this.interval = setInterval(() => {
            if (this.currentStep < this.data.moves.length - 1) {
                this.currentStep++;
                this._renderCurrentState();
                this._updateInfo();
            } else {
                this.stop();
            }
        }, speed);
    },

    stop() {
        this.playing = false;
        replayPlay.innerHTML = '&#9654;';  // play icon
        if (this.interval) {
            clearInterval(this.interval);
            this.interval = null;
        }
    },

    stepForward() {
        if (!this.data) return;
        this.stop();
        if (this.currentStep < this.data.moves.length - 1) {
            this.currentStep++;
            this._renderCurrentState();
            this._updateInfo();
        }
    },

    stepBack() {
        if (!this.data) return;
        this.stop();
        if (this.currentStep >= 0) {
            this.currentStep--;
            this._renderCurrentState();
            this._updateInfo();
        }
    },

    _getCurrentState() {
        if (this.currentStep < 0) return this.data.initial_state;
        return this.data.moves[this.currentStep].state_after;
    },

    _getLastMove() {
        if (this.currentStep < 0) return null;
        return this.data.moves[this.currentStep];
    },

    _updateInfo() {
        const total = this.data.moves.length;
        const current = this.currentStep + 1;
        replayTurn.textContent = `Turn ${current} / ${total}`;

        if (this.currentStep === total - 1 && this.data.winner != null) {
            replayWinner.textContent = `Player ${this.data.winner} wins`;
            replayWinner.style.color = this.data.winner === 1 ? 'var(--cyan)' : 'var(--red)';
        } else if (this.currentStep === total - 1 && this.data.winner == null) {
            replayWinner.textContent = 'Draw';
            replayWinner.style.color = 'var(--text-dim)';
        } else {
            replayWinner.textContent = '';
        }
    },

    _renderCurrentState() {
        const state = this._getCurrentState();
        const lastMove = this._getLastMove();

        if (this.data.game_type === 'board') {
            this._renderBoardState(state, lastMove);
        } else {
            this._renderCardState(state, lastMove);
        }
    },

    // ── Mechanic diff ──────────────────────────────────────────────────────

    _getMechanicBoardChanges() {
        /**
         * Compare state_before_mechanics vs state_after for the current move.
         * Returns a Set of "r,c" strings for board cells the mechanic changed.
         */
        const changes = new Set();
        const moveData = this._getLastMove();
        if (!moveData || !moveData.state_before_mechanics) return changes;

        const before = moveData.state_before_mechanics.board;
        const after  = moveData.state_after.board;
        if (!before || !after) return changes;

        for (let r = 0; r < before.length; r++) {
            for (let c = 0; c < before[r].length; c++) {
                if (before[r][c] !== after[r][c]) {
                    changes.add(`${r},${c}`);
                }
            }
        }
        return changes;
    },

    _getMechanicScoreChanges() {
        /**
         * Compare scores in state_before_mechanics vs state_after.
         * Returns a Set of player numbers whose score the mechanic changed.
         */
        const changes = new Set();
        const moveData = this._getLastMove();
        if (!moveData || !moveData.state_before_mechanics) return changes;

        const beforeScores = moveData.state_before_mechanics.scores;
        const afterScores  = moveData.state_after.scores;
        if (!beforeScores || !afterScores) return changes;

        for (const p of [1, 2]) {
            const bKey = beforeScores[p] != null ? p : String(p);
            const aKey = afterScores[p] != null ? p : String(p);
            if (beforeScores[bKey] !== afterScores[aKey]) {
                changes.add(p);
            }
        }
        return changes;
    },

    // ── Board rendering ─────────────────────────────────────────────────────

    _initBoardGrid() {
        boardGrid.innerHTML = '';
        for (let r = 0; r < 6; r++) {
            for (let c = 0; c < 6; c++) {
                const cell = document.createElement('div');
                cell.className = 'board-cell';
                cell.dataset.row = r;
                cell.dataset.col = c;
                boardGrid.appendChild(cell);
            }
        }
    },

    _renderBoardState(state, lastMove) {
        const board = state.board;
        if (!board) return;

        // Parse the last move to highlight the placed cell
        let highlightR = -1, highlightC = -1;
        if (lastMove && typeof lastMove.move === 'string') {
            const parts = lastMove.move.split(' ');
            if (parts.length === 2) {
                const coords = parts[1].split(',');
                highlightR = parseInt(coords[0], 10);
                highlightC = parseInt(coords[1], 10);
            }
        }

        // Get cells changed by the mechanic (purple highlight)
        const mechanicChanges = this._getMechanicBoardChanges();

        const cells = boardGrid.querySelectorAll('.board-cell');
        let idx = 0;
        for (let r = 0; r < board.length; r++) {
            for (let c = 0; c < board[r].length; c++) {
                const cell = cells[idx++];
                if (!cell) continue;
                const val = board[r][c];
                cell.textContent = val === '_' ? '' : val;
                cell.className = 'board-cell';
                if (val === 'X') cell.classList.add('x');
                if (val === 'O') cell.classList.add('o');
                if (r === highlightR && c === highlightC) {
                    cell.classList.add('highlight');
                }
                if (mechanicChanges.has(`${r},${c}`)) {
                    cell.classList.add('mechanic-changed');
                }
            }
        }
    },

    // ── Card rendering ──────────────────────────────────────────────────────

    _renderCardState(state, lastMove) {
        const hands  = state.hands;
        const scores = state.scores;
        if (!hands || !scores) return;

        const lastPlayed = state.last_played;
        const lastPlayer = lastMove ? lastMove.player : null;

        // Get scores changed by the mechanic (purple highlight)
        const mechanicScoreChanges = this._getMechanicScoreChanges();

        for (const p of [1, 2]) {
            const handEl = document.querySelector(`#card-hand-${p} .hand-cards`);
            const scoreContainer = document.querySelector(`#card-hand-${p} .hand-score`);
            const scoreEl = scoreContainer.querySelector('span');

            handEl.innerHTML = '';
            const hand = hands[p] || hands[String(p)] || [];
            hand.forEach(val => {
                const chip = document.createElement('span');
                chip.className = 'card-chip';
                chip.textContent = val;
                handEl.appendChild(chip);
            });

            scoreEl.textContent = scores[p] || scores[String(p)] || 0;

            // Highlight score if the mechanic changed it
            if (mechanicScoreChanges.has(p)) {
                scoreContainer.classList.add('mechanic-changed');
            } else {
                scoreContainer.classList.remove('mechanic-changed');
            }
        }
    }
};

// ── Mechanic Tutorial Player ────────────────────────────────────────────────
//
// Finds the first turn in the replay where the mechanic actually fired
// (state_before_mechanics differs from state_after) and loops a
// BEFORE → AFTER animation in the tutorial panel.

const tutorialPlayer = {
    beforeBoard:  null,
    afterBoard:   null,
    changedCells: new Set(),   // Set of "r,c" strings affected by the mechanic
    placedCell:   null,        // "r,c" of the piece that triggered the mechanic
    phase:        'before',
    interval:     null,
    _generation:  0,           // incremented on every stop; callbacks bail if theirs is stale

    // How long to hold each phase before flipping (ms)
    BEFORE_MS: 2000,
    AFTER_MS:  2800,

    _stopLoop() {
        this._generation++;            // invalidate every in-flight callback
        clearTimeout(this.interval);
        this.interval = null;
        if (tutorialGrid) tutorialGrid.classList.remove('fading');
    },

    reset() {
        this._stopLoop();
        this.interval    = null;
        this.beforeBoard = null;
        this.afterBoard  = null;
        this.changedCells = new Set();
        this.placedCell  = null;
        this.triggerType  = 'board';
        this.bonusMove    = null;
        this.phase        = 'before';
        tutorialContent.classList.add('hidden');
        tutorialNoTrigger.classList.add('hidden');
        tutorialEmptyState.classList.remove('hidden');
        tutorialEmptyState.querySelector('span').textContent =
            'Waiting for a mechanic to compile...';
    },

    load(d) {
        // Card game: show a placeholder for now (board grid only)
        if (d.game_type !== 'board') {
            tutorialEmptyState.classList.remove('hidden');
            tutorialEmptyState.querySelector('span').textContent =
                'Tutorial view is available for the board game.';
            tutorialContent.classList.add('hidden');
            return;
        }

        // Scan the replay for the first turn where the mechanic had a visible effect.
        // Three passes in priority order:
        //
        //  Pass 1 — board cells changed (e.g. flip, capture)
        //           Compare state_before_mechanics vs state_after for each move.
        //
        //  Pass 2 — extra turn granted (e.g. bonus turn on center placement)
        //           extra_turn is always reset to False in get_state(), so it's
        //           invisible in state diffs. The reliable signal is in the replay
        //           itself: the same player appears twice in a row in the move list.
        //
        //  Pass 3 — custom_state changed between consecutive turns
        //           Compare state_after[i-1].custom_state vs state_after[i].custom_state.

        const trigger = detectMechanicTrigger(d.moves);

        // Show mechanic name + description header
        tutorialEmptyState.classList.add('hidden');
        tutorialContent.classList.remove('hidden');
        tutorialMechLabel.textContent = d.mechanic_name || '';
        tutorialCaption.textContent   = d.mechanic_description || '';

        if (!trigger) {
            // Mechanic compiled and ran but never visibly changed the board.
            // Stop any loop that was running for the previous mechanic.
            this._stopLoop();
            tutorialPhaseLabel.classList.add('hidden');
            tutorialNoTrigger.classList.remove('hidden');
            tutorialGrid.innerHTML = '';
            return;
        }

        tutorialPhaseLabel.classList.remove('hidden');

        tutorialNoTrigger.classList.add('hidden');

        this.beforeBoard  = trigger.before;
        this.afterBoard   = trigger.after;
        this.changedCells = new Set(trigger.changes);
        this.triggerType  = trigger.type;   // 'board' | 'extra_turn' | 'custom_state'
        this.bonusMove    = trigger.bonusMove || null;  // second placement for extra_turn

        // Parse the placed cell from the move string (e.g. "X 2,3" → "2,3")
        this.placedCell = this._parseMovePos(trigger.move);

        this._initGrid();
        this._stopLoop();         // cancel any loop still running from the last mechanic
        this.phase = 'before';
        this._renderPhase();
        this._startLoop();
    },

    // Parse a board-game move string like "X 2,3" → "2,3", or null if unparseable
    _parseMovePos(moveStr) {
        if (typeof moveStr !== 'string') return null;
        const parts = moveStr.split(' ');
        if (parts.length === 2) return parts[1];
        return null;
    },

    _initGrid() {
        tutorialGrid.innerHTML = '';
        for (let r = 0; r < 6; r++) {
            for (let c = 0; c < 6; c++) {
                const cell = document.createElement('div');
                cell.className    = 'tutorial-cell';
                cell.dataset.pos  = `${r},${c}`;
                tutorialGrid.appendChild(cell);
            }
        }
    },

    _renderPhase() {
        const board = this.phase === 'before' ? this.beforeBoard : this.afterBoard;

        // Update phase label — non-board triggers get a more descriptive "after" label
        if (this.phase === 'before') {
            tutorialPhaseLabel.textContent = 'BEFORE';
            tutorialPhaseLabel.className   = 'tutorial-phase-label phase-before';
        } else {
            const afterLabel = {
                board:        'AFTER MECHANIC',
                extra_turn:   'EXTRA TURN GRANTED',
                custom_state: 'STATE UPDATED',
            }[this.triggerType] || 'AFTER MECHANIC';
            tutorialPhaseLabel.textContent = afterLabel;
            tutorialPhaseLabel.className   = 'tutorial-phase-label phase-after';
        }

        const cells = tutorialGrid.querySelectorAll('.tutorial-cell');
        let idx = 0;
        for (let r = 0; r < board.length; r++) {
            for (let c = 0; c < board[r].length; c++) {
                const cell = cells[idx++];
                if (!cell) continue;
                const val = board[r][c];
                const pos = `${r},${c}`;

                cell.textContent = val === '_' ? '' : val;
                cell.className   = 'tutorial-cell';
                if (val === 'X') cell.classList.add('x');
                if (val === 'O') cell.classList.add('o');

                // "Before" phase: highlight the piece that was just placed
                if (this.phase === 'before' && pos === this.placedCell) {
                    cell.classList.add('highlight');
                }

                if (this.phase === 'after') {
                    if (this.triggerType === 'board' && this.changedCells.has(pos)) {
                        // Board-changing mechanic: highlight the affected cells
                        cell.classList.add('mechanic-changed');
                    } else if (this.triggerType === 'extra_turn' && pos === this._parseMovePos(this.bonusMove)) {
                        // Extra-turn mechanic: pulse the bonus placement
                        cell.classList.add('mechanic-changed');
                    } else if (this.triggerType === 'custom_state' && pos === this.placedCell) {
                        // Custom-state mechanic: pulse the triggering piece
                        cell.classList.add('mechanic-changed');
                    }
                }
            }
        }
    },

    _startLoop() {
        const self = this;
        const gen = ++self._generation;   // capture this loop's generation number

        const schedule = () => {
            if (gen !== self._generation) return;   // a newer loop has taken over
            const holdMs = self.phase === 'before' ? self.BEFORE_MS : self.AFTER_MS;
            self.interval = setTimeout(() => {
                if (gen !== self._generation) return;
                // Fade out
                tutorialGrid.classList.add('fading');
                setTimeout(() => {
                    if (gen !== self._generation) {
                        // Stopped mid-fade — restore visibility and exit
                        tutorialGrid.classList.remove('fading');
                        return;
                    }
                    // Flip phase and render
                    self.phase = self.phase === 'before' ? 'after' : 'before';
                    self._renderPhase();
                    // Fade back in
                    tutorialGrid.classList.remove('fading');
                    // Schedule next flip
                    schedule();
                }, 260);   // matches the CSS transition duration
            }, holdMs);
        };
        schedule();
    },
};


// ── Library Manager ─────────────────────────────────────────────────────────
//
// Manages the Library tab: fetches saved cards on load, adds new ones live
// when a mechanic_accepted event arrives, handles card expand/collapse, and
// runs a per-card nano tutorial animation when a card is expanded.

const libraryManager = {
    cards:       [],    // array of card data objects (same shape as library_cards.json)
    expandedId:  null,  // index of the currently expanded card (or null)
    _animations: {},    // map of card-id → animation state object

    // DOM refs for the library view
    get _emptyEl()  { return document.getElementById('library-empty'); },
    get _gridEl()   { return document.getElementById('library-grid'); },

    // ── Public API ──────────────────────────────────────────────────────────

    async init() {
        try {
            const res = await fetch('/api/library-cards');
            if (!res.ok) return;
            const cards = await res.json();
            cards.forEach(c => this._addCard(c));
        } catch (e) { /* server may not be running yet */ }
    },

    addLive(card) {
        this._addCard(card);
    },

    // ── Private helpers ─────────────────────────────────────────────────────

    _addCard(card) {
        const id = this.cards.length;
        this.cards.push(card);
        this._emptyEl.classList.add('hidden');
        this._renderCard(id, card);
    },

    _renderCard(id, card) {
        const el = document.createElement('div');
        el.className = 'lib-card';
        el.dataset.id = id;

        el.innerHTML = `
            <div class="lib-card-header">
                <span class="lib-card-name">${escapeHtml(card.mechanic_name || '')}</span>
                <span class="lib-card-chevron">&#9660;</span>
            </div>
            <div class="lib-card-scores">
                ${scoreBar('Balance', 1 - (card.scores?.balance_gap ?? 1))}
                ${scoreBar('Depth',   card.scores?.depth   ?? 0)}
                <hr class="score-divider">
                ${scoreBar('Aggregate', card.scores?.aggregate ?? 0)}
            </div>
            <div class="lib-card-expanded-content hidden">
                <div class="lib-card-desc">${escapeHtml(card.description || '')}</div>
                <div class="lib-card-tutorial">
                    <div class="lib-phase-label phase-before">BEFORE</div>
                    <div class="lib-tutorial-grid"></div>
                </div>
            </div>`;

        el.addEventListener('click', () => this._toggleCard(id));
        this._gridEl.appendChild(el);
    },

    _staticBoard(board, trigger) {
        if (!board) return '<div class="lib-no-trigger">No board data</div>';
        const placedPos = trigger ? this._parseMovePos(trigger.move) : null;
        let html = '<div class="lib-preview-grid">';
        for (let r = 0; r < board.length; r++) {
            for (let c = 0; c < board[r].length; c++) {
                const val = board[r][c];
                const pos = `${r},${c}`;
                let cls = 'lib-cell';
                if (val === 'X') cls += ' x';
                if (val === 'O') cls += ' o';
                if (pos === placedPos) cls += ' highlight';
                html += `<div class="${cls}">${val === '_' ? '' : escapeHtml(String(val))}</div>`;
            }
        }
        html += '</div>';
        return html;
    },

    _toggleCard(id) {
        if (this.expandedId === id) {
            this._collapseCard(id);
            this.expandedId = null;
        } else {
            if (this.expandedId !== null) this._collapseCard(this.expandedId);
            this._expandCard(id);
            this.expandedId = id;
        }
    },

    _expandCard(id) {
        const el = document.querySelector(`.lib-card[data-id="${id}"]`);
        if (!el) return;
        el.classList.add('expanded');
        el.querySelector('.lib-card-expanded-content').classList.remove('hidden');
        this._startAnimation(id, el);
    },

    _collapseCard(id) {
        const el = document.querySelector(`.lib-card[data-id="${id}"]`);
        if (!el) return;
        el.classList.remove('expanded');
        el.querySelector('.lib-card-expanded-content').classList.add('hidden');
        this._stopAnimation(id);
    },

    // ── Per-card animation (same generation-counter pattern as tutorialPlayer) ──

    _stopAnimation(id) {
        const anim = this._animations[id];
        if (!anim) return;
        anim.generation++;
        clearTimeout(anim.timeout);
        const gridEl = document.querySelector(`.lib-card[data-id="${id}"] .lib-tutorial-grid`);
        if (gridEl) gridEl.classList.remove('fading');
        delete this._animations[id];
    },

    _startAnimation(id, cardEl) {
        const card = this.cards[id];
        if (!card || !card.replay) return;

        const trigger = detectMechanicTrigger(card.replay.moves);
        const gridEl      = cardEl.querySelector('.lib-tutorial-grid');
        const phaseLabelEl = cardEl.querySelector('.lib-phase-label');

        if (!trigger) {
            gridEl.innerHTML = '<div class="lib-no-trigger">Not triggered in this replay</div>';
            return;
        }

        // Build 6x6 grid
        gridEl.innerHTML = '';
        for (let r = 0; r < 6; r++) {
            for (let c = 0; c < 6; c++) {
                const cell = document.createElement('div');
                cell.className   = 'lib-cell';
                cell.dataset.pos = `${r},${c}`;
                gridEl.appendChild(cell);
            }
        }

        const anim = { generation: 0, timeout: null, phase: 'before',
                        BEFORE_MS: 2000, AFTER_MS: 2800 };
        this._animations[id] = anim;

        const renderPhase = () => {
            const board = anim.phase === 'before' ? trigger.before : trigger.after;

            if (anim.phase === 'before') {
                phaseLabelEl.textContent = 'BEFORE';
                phaseLabelEl.className   = 'lib-phase-label phase-before';
            } else {
                const labels = { board: 'AFTER MECHANIC', extra_turn: 'EXTRA TURN',
                                 custom_state: 'STATE UPDATED' };
                phaseLabelEl.textContent = labels[trigger.type] || 'AFTER MECHANIC';
                phaseLabelEl.className   = 'lib-phase-label phase-after';
            }

            const cells = gridEl.querySelectorAll('.lib-cell');
            let idx = 0;
            for (let r = 0; r < board.length; r++) {
                for (let c = 0; c < board[r].length; c++) {
                    const cell = cells[idx++];
                    if (!cell) continue;
                    const val = board[r][c];
                    const pos = `${r},${c}`;

                    cell.textContent = val === '_' ? '' : val;
                    cell.className   = 'lib-cell';
                    if (val === 'X') cell.classList.add('x');
                    if (val === 'O') cell.classList.add('o');

                    if (anim.phase === 'before' && pos === this._parseMovePos(trigger.move)) {
                        cell.classList.add('highlight');
                    }
                    if (anim.phase === 'after') {
                        if (trigger.type === 'board' && trigger.changes.has(pos)) {
                            cell.classList.add('mechanic-changed');
                        } else if (trigger.type === 'extra_turn'
                                   && pos === this._parseMovePos(trigger.bonusMove)) {
                            cell.classList.add('mechanic-changed');
                        } else if (trigger.type === 'custom_state'
                                   && pos === this._parseMovePos(trigger.move)) {
                            cell.classList.add('mechanic-changed');
                        }
                    }
                }
            }
        };

        renderPhase();

        const schedule = (gen) => {
            if (gen !== anim.generation) return;
            const holdMs = anim.phase === 'before' ? anim.BEFORE_MS : anim.AFTER_MS;
            anim.timeout = setTimeout(() => {
                if (gen !== anim.generation) return;
                gridEl.classList.add('fading');
                setTimeout(() => {
                    if (gen !== anim.generation) {
                        gridEl.classList.remove('fading');
                        return;
                    }
                    anim.phase = anim.phase === 'before' ? 'after' : 'before';
                    renderPhase();
                    gridEl.classList.remove('fading');
                    schedule(gen);
                }, 260);
            }, holdMs);
        };
        schedule(anim.generation);
    },

    _parseMovePos(moveStr) {
        if (typeof moveStr !== 'string') return null;
        const parts = moveStr.split(' ');
        return parts.length === 2 ? parts[1] : null;
    },
};


// Wire up replay buttons
replayPlay.addEventListener('click', () => {
    if (replayPlayer.playing) replayPlayer.stop();
    else replayPlayer.play();
});
replayBack.addEventListener('click', () => replayPlayer.stepBack());
replayForward.addEventListener('click', () => replayPlayer.stepForward());
replaySpeed.addEventListener('input', () => {
    if (replayPlayer.playing) {
        replayPlayer.stop();
        replayPlayer.play();
    }
});


// ── Tab Switching ────────────────────────────────────────────────────────────

const mainLayout   = document.getElementById('main-layout');
const libraryView  = document.getElementById('library-view');
const tabPipeline  = document.getElementById('tab-pipeline');
const tabLibrary   = document.getElementById('tab-library');

function switchTab(tab) {
    if (tab === 'pipeline') {
        mainLayout.classList.remove('hidden');
        libraryView.classList.add('hidden');
        tabPipeline.classList.add('active');
        tabLibrary.classList.remove('active');
        // Collapse any open library card so its animation stops
        if (libraryManager.expandedId !== null) {
            libraryManager._collapseCard(libraryManager.expandedId);
            libraryManager.expandedId = null;
        }
    } else {
        mainLayout.classList.add('hidden');
        libraryView.classList.remove('hidden');
        tabLibrary.classList.add('active');
        tabPipeline.classList.remove('active');
    }
}

tabPipeline.addEventListener('click', () => switchTab('pipeline'));
tabLibrary.addEventListener('click',  () => switchTab('library'));


// ── Startup ──────────────────────────────────────────────────────────────────

libraryManager.init();
