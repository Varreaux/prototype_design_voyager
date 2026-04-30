/**
 * DesignVoyager Dashboard
 * WebSocket client, event rendering, and game replay player.
 */

// ── DOM references ──────────────────────────────────────────────────────────

const logContent    = document.getElementById('log-content');
const startBtn      = document.getElementById('start-btn');
const stopBtn       = document.getElementById('stop-btn');
const resetBtn      = document.getElementById('reset-btn');
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
    resetBtn.disabled    = running;
    gameSelect.disabled  = running;
    iterInput.disabled   = running;
    topkInput.disabled   = running;
}

resetBtn.addEventListener('click', async () => {
    if (running) return;
    const game = gameSelect.value;
    const fileList = game === 'board'
        ? 'library.json, discarded_board.json, and the board entries in library_cards.json'
        : 'library_card.json, discarded_card.json, and the card entries in library_cards.json';
    const ok = window.confirm(
        `Reset the ${game} game library?\n\n` +
        `This will delete ${fileList}.\n\n` +
        `Discarded names will be cleared too, so Gemini may re-propose them. ` +
        `This cannot be undone.`
    );
    if (!ok) return;

    resetBtn.disabled = true;
    const origLabel   = resetBtn.textContent;
    resetBtn.textContent = 'Resetting...';
    try {
        const res = await fetch('/api/reset-library', {
            method:  'POST',
            headers: {'Content-Type': 'application/json'},
            body:    JSON.stringify({game_name: game}),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok || !body.ok) {
            window.alert(`Reset failed: ${body.error || res.statusText}`);
            return;
        }
        const deleted = (body.deleted || []).join(', ') || 'none';
        window.alert(
            `${game} library reset.\n` +
            `Files removed: ${deleted}\n` +
            `Cards removed from library_cards.json: ${body.cards_removed}`
        );
        // Reset clears the saved AI vs AI loadout (now stale) and the saved
        // pair lab results, so a reload won't restore data tied to the old library.
        if (game === 'card' && typeof aivaiManager !== 'undefined') {
            try { aivaiManager.clearSavedLoadout(); } catch (e) {}
        }
        try { localStorage.removeItem('dv-pairlab-results'); } catch (e) {}
        // Refresh the in-memory library view by reloading.
        window.location.reload();
    } catch (e) {
        window.alert(`Reset failed: ${e}`);
    } finally {
        resetBtn.textContent = origLabel;
        resetBtn.disabled    = running;
    }
});


// ── Event handler dispatch ──────────────────────────────────────────────────

// Cached baseline metrics from the start-of-run baseline playtest.
// Used to render delta indicators next to each per-iteration score bar.
let baselineMetrics = null;

function handleEvent(type, data) {
    switch (type) {
        case 'welcome':           renderWelcome(data); break;
        case 'baseline_start':    renderBaselineStart(data); break;
        case 'baseline_progress': renderBaselineProgress(data); break;
        case 'baseline_result':   renderBaselineResult(data); break;
        case 'iteration_start':   renderIterationStart(data); break;
        case 'retrieve':          renderRetrieve(data); break;
        case 'propose_start':     renderProposeStart(data); break;
        case 'propose_stream':    renderProposeStream(data); break;
        case 'propose_result':    renderProposeResult(data); break;
        case 'compile_result':    renderCompileResult(data); break;
        case 'demo_replay_start': renderDemoReplayStart(data); break;
        case 'demo_replay_done':  renderDemoReplayDone(data); break;
        case 'error_inline':      renderInlineError(data); break;
        case 'playtest_start':    renderPlaytestStart(data); break;
        case 'playtest_progress': renderPlaytestProgress(data); break;
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

function renderBaselineStart(d) {
    const total = d.total_games || 100;
    logContent.insertAdjacentHTML('beforeend', `
        <div class="progress-panel" id="baseline-progress-panel">
            <div class="progress-title">
                <span class="spinner"></span>
                Running baseline playtest <span class="progress-sub">(no mechanic, ${total} games for delta-gated verification)</span>
            </div>
            <div class="progress-bar-container">
                <div class="progress-bar-fill" id="baseline-bar" style="width:0%"></div>
            </div>
            <div class="progress-detail" id="baseline-detail">
                Setting up... this takes 30 to 90 seconds the first time.
            </div>
        </div>`);
}

function renderBaselineProgress(d) {
    const bar    = document.getElementById('baseline-bar');
    const detail = document.getElementById('baseline-detail');
    if (!bar || !detail) return;
    const pct = Math.round((d.completed / d.total) * 100);
    bar.style.width = pct + '%';
    const phaseLabel = d.phase === 'balance' ? 'Balance phase' : 'Depth phase';
    detail.textContent = `${phaseLabel}: game ${d.completed} of ${d.total}`;
}

function renderBaselineResult(d) {
    baselineMetrics = d;
    const panel = document.getElementById('baseline-progress-panel');
    if (panel) panel.remove();

    const balance = 1 - (d.balance_gap || 0);
    logContent.insertAdjacentHTML('beforeend', `
        <div class="baseline-banner">
            <div class="baseline-title">Baseline metrics <span class="baseline-sub">(plain game, no mechanic)</span></div>
            <div class="baseline-grid">
                <span class="bm-label">Playability</span><span class="bm-val">${(d.playability * 100).toFixed(0)}%</span>
                <span class="bm-label">Balance</span><span class="bm-val">${balance.toFixed(2)}</span>
                <span class="bm-label">Depth</span><span class="bm-val">${d.depth.toFixed(2)}</span>
                <span class="bm-label">Decisiveness</span><span class="bm-val">${d.decisiveness.toFixed(2)}</span>
                <span class="bm-label">Agency</span><span class="bm-val">${d.agency.toFixed(2)}</span>
                <span class="bm-label">Avg game length</span><span class="bm-val">${d.avg_game_length.toFixed(1)} turns</span>
            </div>
            <div class="baseline-hint">Each mechanic below is compared against these numbers. A mechanic that does not move them is rejected as a no-op.</div>
        </div>`);
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

// Rotating phase hints shown during the proposal wait so the user has
// something to look at instead of a static spinner.
const PROPOSAL_PHASES = [
    'Reading the game skeleton...',
    'Reviewing context mechanics from the library...',
    'Considering the curriculum stage and banned names...',
    'Drafting a new mechanic concept...',
    'Writing the Python code...',
    'Sanity checking the response...',
    'Almost done...',
];
let _proposalPhaseTimer = null;

function _startProposalPhaseRotator(elementId) {
    let i = 0;
    const el = document.getElementById(elementId);
    if (!el) return;
    el.textContent = PROPOSAL_PHASES[0];
    _proposalPhaseTimer = setInterval(() => {
        i = Math.min(i + 1, PROPOSAL_PHASES.length - 1);
        const elNow = document.getElementById(elementId);
        if (!elNow) {
            clearInterval(_proposalPhaseTimer);
            _proposalPhaseTimer = null;
            return;
        }
        elNow.textContent = PROPOSAL_PHASES[i];
    }, 3500);
}

function _stopProposalPhaseRotator() {
    if (_proposalPhaseTimer) {
        clearInterval(_proposalPhaseTimer);
        _proposalPhaseTimer = null;
    }
}

function renderProposeStart(d) {
    const html = `
        <div class="propose-panel" id="propose-panel">
            <div class="propose-header">
                <span class="spinner"></span>
                <strong>Gemini is designing a new mechanic</strong>
                <span class="propose-context">(using ${d.context_count} mechanics as context)</span>
            </div>
            <div class="propose-phase" id="propose-phase">Connecting to Gemini...</div>
            <pre class="propose-stream" id="propose-stream"></pre>
        </div>`;
    logContent.insertAdjacentHTML('beforeend', html);
    _startProposalPhaseRotator('propose-phase');
}

function renderProposeStream(d) {
    const el = document.getElementById('propose-stream');
    if (!el) return;
    // Show the most recent ~600 chars so very long responses don't blow
    // out the panel. Tail end is what's most interesting to watch arriving.
    const text = d.text || '';
    el.textContent = text.length > 600 ? '... ' + text.slice(-600) : text;
    el.scrollTop = el.scrollHeight;
}

function renderProposeResult(d) {
    // Remove old spinner (compat) and new propose panel
    _stopProposalPhaseRotator();
    const spinner = document.getElementById('propose-spinner');
    if (spinner) spinner.remove();
    const panel = document.getElementById('propose-panel');
    if (panel) panel.remove();

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

// Thin spinner shown between compile_result and playtest_start while the
// recorded demo game runs. Without this, that stretch is silent and looks
// like a freeze. The line gets removed when the demo finishes (or earlier
// if playtest_start arrives first, defensive cleanup in renderPlaytestStart).
function renderDemoReplayStart(d) {
    // Defensive: remove any leftover from a previous iteration.
    const existing = document.getElementById('demo-replay-line');
    if (existing) existing.remove();
    const name = escapeHtml(d.mechanic_name || 'mechanic');
    logContent.insertAdjacentHTML('beforeend', `
        <div class="compile-line" id="demo-replay-line">
            <span class="spinner"></span>
            Recording demo replay for <strong>${name}</strong>...
        </div>`);
}

function renderDemoReplayDone(_d) {
    const el = document.getElementById('demo-replay-line');
    if (el) el.remove();
}

// Inline error notice that does NOT end the run. Used for non-fatal failures
// (e.g. demo replay crashed but the full playtest can still proceed).
function renderInlineError(d) {
    logContent.insertAdjacentHTML('beforeend', `
        <div class="compile-line fail">${escapeHtml(d.message || 'Inline error')}</div>`);
}

function renderPlaytestStart(d) {
    // Defensive: clear the demo-replay spinner if it is still around.
    const demo = document.getElementById('demo-replay-line');
    if (demo) demo.remove();
    logContent.insertAdjacentHTML('beforeend', `
        <div class="progress-panel" id="playtest-progress-panel">
            <div class="progress-title">
                <span class="spinner"></span>
                Playtesting <strong>${d.mechanic_name}</strong> <span class="progress-sub">(100 games)</span>
            </div>
            <div class="progress-bar-container">
                <div class="progress-bar-fill" id="playtest-bar" style="width:0%"></div>
            </div>
            <div class="progress-detail" id="playtest-detail">
                Starting...
            </div>
        </div>`);
}

function renderPlaytestProgress(d) {
    const bar    = document.getElementById('playtest-bar');
    const detail = document.getElementById('playtest-detail');
    if (!bar || !detail) return;
    // The two phases (balance, depth) each go 0..100% of their own count.
    // Compose them into a single 0..100% by treating balance as 0..60% of
    // overall and depth as 60..100%.
    const balanceWeight = 0.6;
    const overallPct = d.phase === 'balance'
        ? (d.completed / d.total) * balanceWeight * 100
        : balanceWeight * 100 + (d.completed / d.total) * (1 - balanceWeight) * 100;
    bar.style.width = overallPct.toFixed(0) + '%';
    const phaseLabel = d.phase === 'balance' ? 'Balance phase' : 'Depth phase';
    detail.textContent = `${phaseLabel}: game ${d.completed} of ${d.total}`;
}

function renderPlaytestResult(d) {
    const spinner = document.getElementById('playtest-spinner');
    if (spinner) spinner.remove();
    const ppanel = document.getElementById('playtest-progress-panel');
    if (ppanel) ppanel.remove();

    const s        = d.scores       || {};
    const abs      = d.absolute_metrics || {};
    const delta    = d.delta_metrics    || {};
    const trig     = d.trigger_stats    || {};
    const rel      = d.relative_score   || 0;
    const stageThr = d.stage_threshold  || 0.03;
    const balance  = 1 - (s.balance_gap || 0);
    const failures = d.failure_modes    || [];

    // Detect "early-fail" cases: when an absolute behavioral gate failed,
    // delta_metrics is empty and relative_score is 0 — but the real reason
    // is something like extreme_imbalance, NOT that the mechanic is a no-op.
    const onlyNoOpFailure = failures.length === 1 && failures[0] === 'negative_relative_gain';
    const earlyFailMode = (failures.length > 0 && !onlyNoOpFailure) ? failures[0] : null;

    // Playability gate uses the new 0.85 threshold (relaxed from binary 1.0)
    // because the playtest now runs all 100 games rather than stopping early.
    const playPass = (s.playability || 0) >= 0.85;
    const playGate = playPass
        ? `<div class="playability-gate pass">Playability gate &#10003; passed (${((s.playability||0) * 100).toFixed(0)}%)</div>`
        : `<div class="playability-gate fail">Playability gate &#10007; failed (${((s.playability||0) * 100).toFixed(0)}%)</div>`;

    // Integration-stage failures (compile/schema-level) leave us with no
    // real playtest metrics, so the relative score is meaningless and we
    // show n/a. Behavioral-stage failures (low_playability,
    // extreme_imbalance, etc.) DO have real metrics, so we now compute
    // and display a diagnostic relative score with a "failed earlier
    // check" caveat instead of n/a.
    const INTEGRATION_FAIL_MODES = new Set([
        'schema_failure', 'syntax_failure', 'hook_failure',
        'instantiation_failure', 'dry_run_failure',
    ]);
    const isIntegrationFail = failures.some(f => INTEGRATION_FAIL_MODES.has(f));

    // Build the relative-gain banner. There are four cases to render:
    //   1. integration-level early fail: no playtest metrics, show n/a.
    //   2. behavioral-level early fail: real number with a caveat.
    //   3. failures contains negative_relative_gain: this IS a no-op.
    //   4. No failures: above-threshold, accepted.
    let relColor, relLabel, relValue;
    if (earlyFailMode && isIntegrationFail) {
        relColor = 'red';
        relValue = 'n/a';
        relLabel = `Skipped — compile-level failure (${earlyFailMode.replace(/_/g, ' ')})`;
    } else if (earlyFailMode) {
        relColor = 'red';
        relValue = `${rel >= 0 ? '+' : ''}${rel.toFixed(3)}`;
        relLabel = `Failed earlier check (${earlyFailMode.replace(/_/g, ' ')})`;
    } else if (onlyNoOpFailure || (rel < stageThr && Math.abs(rel) < 0.001)) {
        relColor = 'red';
        relValue = `${rel >= 0 ? '+' : ''}${rel.toFixed(3)}`;
        relLabel = `Below stage threshold (${stageThr.toFixed(2)}) — looks like a no-op`;
    } else if (rel < stageThr) {
        relColor = 'yellow';
        relValue = `${rel >= 0 ? '+' : ''}${rel.toFixed(3)}`;
        relLabel = `Below stage threshold (${stageThr.toFixed(2)})`;
    } else {
        relColor = 'green';
        relValue = `+${rel.toFixed(3)}`;
        relLabel = `Above stage threshold (${stageThr.toFixed(2)})`;
    }

    const relBanner = `
        <div class="relative-gain ${relColor}">
            <span class="rg-label">Relative gain vs baseline</span>
            <span class="rg-value">${relValue}</span>
            <span class="rg-detail">${relLabel}</span>
        </div>`;

    const html = `
        <div class="scores-section">
            ${playGate}
            ${triggerGate(trig)}
            ${scoreBarWithDelta('Balance',      balance,                delta.delta_balance_gap, true)}
            ${scoreBarWithDelta('Depth',        s.depth || 0,           delta.delta_depth,       false)}
            ${scoreBarWithDelta('Decisiveness', abs.decisiveness || 0,  delta.delta_decisiveness,false)}
            ${scoreBarWithDelta('Agency',       abs.agency || 0,        delta.delta_agency,      false)}
            <hr class="score-divider">
            ${scoreBar('Aggregate', s.aggregate || 0)}
            ${relBanner}
        </div>`;
    logContent.insertAdjacentHTML('beforeend', html);
}

// Score bar with a ±delta tag next to the number.
// invertDelta=true means smaller deltas are better (e.g. balance_gap going down is good).
function scoreBarWithDelta(label, value, deltaVal, invertDelta) {
    const pct   = Math.max(0, Math.min(100, value * 100));
    const color = value >= 0.75 ? 'green' : (value >= 0.5 ? 'yellow' : 'red');

    let deltaHtml = '';
    if (deltaVal != null && Math.abs(deltaVal) > 0.001) {
        // For balance, the underlying delta is delta_balance_gap, where smaller
        // gap is better. Flip the sign so the visible delta on the Balance row
        // reads "+" when balance got better.
        const shown = invertDelta ? -deltaVal : deltaVal;
        const dColor = shown > 0 ? 'green' : (shown < 0 ? 'red' : 'dim');
        const sign   = shown > 0 ? '+' : '';
        deltaHtml = `<span class="delta-tag ${dColor}">${sign}${shown.toFixed(2)} vs baseline</span>`;
    } else if (deltaVal != null) {
        deltaHtml = `<span class="delta-tag dim">~ baseline</span>`;
    }

    return `
        <div class="score-row">
            <span class="score-label">${label}</span>
            <div class="score-bar-container">
                <div class="score-bar-fill ${color}" style="width:${pct}%"></div>
            </div>
            <span class="score-value">${value.toFixed(2)}</span>
            ${deltaHtml}
        </div>`;
}

// Trigger gate. In practice the underlying number is almost always 0% or
// 100% (a mechanic's condition either fires across many matches or never
// fires at all), so we render it as a binary pass/fail line styled like
// the Playability gate rather than as a half-empty/half-full bar. The
// rule: as long as state_changed_matches > 0, the mechanic did SOMETHING
// in at least one match, so it passes. Zero matches with effect fails.
function triggerGate(trig) {
    const total = trig.total_matches != null ? trig.total_matches : 0;
    const effMatches = trig.state_changed_matches != null
        ? trig.state_changed_matches
        : (trig.triggered_matches != null ? trig.triggered_matches : 0);
    if (total <= 0) {
        return `<div class="playability-gate fail">Trigger gate &#10007; failed (no playtest data)</div>`;
    }
    if (effMatches > 0) {
        return `<div class="playability-gate pass">Trigger gate &#10003; passed (${effMatches}/${total} matches with effect)</div>`;
    }
    return `<div class="playability-gate fail">Trigger gate &#10007; failed (0/${total} matches with effect)</div>`;
}

function renderVerifyResult(d) {
    const decision = d.decision;
    let title, detail;

    if (decision === 'accept') {
        const agg = d.scores && d.scores.aggregate != null
            ? `aggregate score: ${d.scores.aggregate.toFixed(2)}`
            : '';
        title = '&#10003; ACCEPTED';
        detail = `Mechanic moved metrics off baseline. ${agg}`;
    } else if (decision === 'revise') {
        title = '&rarr; REVISING';
        detail = d.feedback || 'Sending feedback to Gemini for one revision attempt...';
    } else {
        title = '&#10007; DISCARDED';
        detail = d.feedback || 'Could not produce a working mechanic.';
    }

    logContent.insertAdjacentHTML('beforeend', `
        <div class="verdict-panel ${decision}">
            <div class="verdict-title">${title}</div>
            <div class="verdict-detail">${escapeHtml(detail)}</div>
        </div>`);
}

function renderRevisionStart(d) {
    logContent.insertAdjacentHTML('beforeend', `
        <div class="propose-panel" id="propose-panel">
            <div class="propose-header">
                <span class="spinner"></span>
                <strong>Gemini is revising ${d.mechanic_name}</strong>
            </div>
            <div class="propose-phase" id="propose-phase">Reading the failure feedback...</div>
            <pre class="propose-stream" id="propose-stream"></pre>
        </div>`);
    _startProposalPhaseRotator('propose-phase');
}

function renderRevisionResult(d) {
    _stopProposalPhaseRotator();
    const spinner = document.getElementById('revision-spinner');
    if (spinner) spinner.remove();
    const panel = document.getElementById('propose-panel');
    if (panel) panel.remove();

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

// Auto-scroll only when the user is already near the bottom. If they have
// scrolled up to read earlier content, do not yank them back down on every
// incoming event. A 64px threshold counts as "near the bottom" so the user
// doesn't have to be pixel-perfect to keep auto-following.
const AUTO_SCROLL_THRESHOLD_PX = 64;

function autoScroll() {
    const log = document.getElementById('pipeline-log');
    if (!log) return;
    const distanceFromBottom = log.scrollHeight - log.scrollTop - log.clientHeight;
    if (distanceFromBottom <= AUTO_SCROLL_THRESHOLD_PX) {
        log.scrollTop = log.scrollHeight;
    }
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


// Card-game variant of detectMechanicTrigger. Scans a card replay for the
// first turn where the mechanic changed hands, scores, or custom_state.
// Returns { type, before, after, handChanges, scoreChanges, move, player }
// or null. handChanges and scoreChanges are { 1: bool, 2: bool }.
function detectCardMechanicTrigger(moves) {
    if (!moves || moves.length === 0) return null;

    const handsEqual = (a, b) => JSON.stringify(a || []) === JSON.stringify(b || []);
    const getHand   = (state, p) => (state.hands || {})[p] || (state.hands || {})[String(p)] || [];
    const getScore  = (state, p) => (state.scores || {})[p] || (state.scores || {})[String(p)] || 0;

    // Pass 1: hands or scores changed by mechanic in a single turn
    for (const move of moves) {
        if (!move.state_before_mechanics || !move.state_after) continue;
        const before = move.state_before_mechanics;
        const after  = move.state_after;
        const handChanges  = { 1: false, 2: false };
        const scoreChanges = { 1: false, 2: false };
        for (const p of [1, 2]) {
            if (!handsEqual(getHand(before, p), getHand(after, p))) handChanges[p]  = true;
            if (getScore(before, p) !== getScore(after, p))         scoreChanges[p] = true;
        }
        if (handChanges[1] || handChanges[2] || scoreChanges[1] || scoreChanges[2]) {
            return {
                type: 'card', before, after,
                handChanges, scoreChanges,
                move: move.move, player: move.player,
            };
        }
    }

    // Pass 2: extra turn -- same player twice in a row
    for (let i = 0; i < moves.length - 1; i++) {
        if (moves[i].player === moves[i + 1].player) {
            return {
                type: 'card_extra_turn',
                before: moves[i].state_after,
                after:  moves[i + 1].state_after,
                handChanges:  { 1: false, 2: false },
                scoreChanges: { 1: false, 2: false },
                move: moves[i].move, player: moves[i].player,
            };
        }
    }

    // Pass 3: custom_state changed between turns
    for (let i = 1; i < moves.length; i++) {
        const prev = moves[i - 1].state_after;
        const curr = moves[i].state_after;
        if (JSON.stringify(prev.custom_state) !== JSON.stringify(curr.custom_state)) {
            return {
                type: 'card_custom_state',
                before: prev, after: curr,
                handChanges:  { 1: false, 2: false },
                scoreChanges: { 1: false, 2: false },
                move: moves[i].move, player: moves[i].player,
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
        this.gameType    = 'board';
        this.beforeBoard = null;
        this.afterBoard  = null;
        this.changedCells = new Set();
        this.placedCell  = null;
        this.triggerType  = 'board';
        this.bonusMove    = null;
        this.cardBefore   = null;
        this.cardAfter    = null;
        this.cardChanges  = null;
        this.phase        = 'before';
        tutorialGrid.classList.remove('card-layout');
        tutorialContent.classList.add('hidden');
        tutorialNoTrigger.classList.add('hidden');
        tutorialEmptyState.classList.remove('hidden');
        tutorialEmptyState.querySelector('span').textContent =
            'Waiting for a mechanic to compile...';
    },

    load(d) {
        if (d.game_type === 'card') {
            this._loadCard(d);
            return;
        }
        if (d.game_type !== 'board') {
            tutorialEmptyState.classList.remove('hidden');
            tutorialEmptyState.querySelector('span').textContent =
                'Tutorial view not available for this game type.';
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

        this.gameType     = 'board';
        this.beforeBoard  = trigger.before;
        this.afterBoard   = trigger.after;
        this.changedCells = new Set(trigger.changes);
        this.triggerType  = trigger.type;   // 'board' | 'extra_turn' | 'custom_state'
        this.bonusMove    = trigger.bonusMove || null;  // second placement for extra_turn

        // Parse the placed cell from the move string (e.g. "X 2,3" → "2,3")
        this.placedCell = this._parseMovePos(trigger.move);

        tutorialGrid.classList.remove('card-layout');
        this._initGrid();
        this._stopLoop();         // cancel any loop still running from the last mechanic
        this.phase = 'before';
        this._renderPhase();
        this._startLoop();
    },

    // Card-game version of load(). Same shape: detect a trigger, set up
    // the layout, kick off a before/after animation loop. Differences:
    // we render two hand+score blocks instead of a 6x6 grid, and the
    // "changes" we highlight are per-player hand and score deltas.
    _loadCard(d) {
        const trigger = detectCardMechanicTrigger(d.moves);

        tutorialEmptyState.classList.add('hidden');
        tutorialContent.classList.remove('hidden');
        tutorialMechLabel.textContent = d.mechanic_name || '';
        tutorialCaption.textContent   = d.mechanic_description || '';

        if (!trigger) {
            this._stopLoop();
            tutorialPhaseLabel.classList.add('hidden');
            tutorialNoTrigger.classList.remove('hidden');
            tutorialGrid.innerHTML = '';
            return;
        }

        tutorialPhaseLabel.classList.remove('hidden');
        tutorialNoTrigger.classList.add('hidden');

        this.gameType    = 'card';
        this.cardBefore  = trigger.before;
        this.cardAfter   = trigger.after;
        this.cardChanges = trigger;
        this.triggerType = trigger.type;

        this._initCardLayout();
        this._stopLoop();
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

    _initCardLayout() {
        tutorialGrid.classList.add('card-layout');
        tutorialGrid.innerHTML = `
            <div class="tut-card-side">
                <div class="tut-card-label">Player 1</div>
                <div class="tut-card-hand" id="tut-hand-1"></div>
                <div class="tut-card-score">Score: <span id="tut-score-1">0</span></div>
            </div>
            <div class="tut-card-vs">vs</div>
            <div class="tut-card-side">
                <div class="tut-card-label">Player 2</div>
                <div class="tut-card-hand" id="tut-hand-2"></div>
                <div class="tut-card-score">Score: <span id="tut-score-2">0</span></div>
            </div>`;
    },

    _renderPhase() {
        if (this.gameType === 'card') {
            this._renderCardPhase();
            return;
        }
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

    // Card-game phase render. Mirrors _renderPhase but draws hands+scores
    // instead of board cells. In the after phase, we add .mechanic-changed
    // to the hand block or the score chip for any player whose hand or
    // score moved (handChanges / scoreChanges from the trigger).
    _renderCardPhase() {
        const state = this.phase === 'before' ? this.cardBefore : this.cardAfter;
        if (!state) return;

        if (this.phase === 'before') {
            tutorialPhaseLabel.textContent = 'BEFORE';
            tutorialPhaseLabel.className   = 'tutorial-phase-label phase-before';
        } else {
            const afterLabel = {
                card:              'AFTER MECHANIC',
                card_extra_turn:   'EXTRA TURN GRANTED',
                card_custom_state: 'STATE UPDATED',
            }[this.triggerType] || 'AFTER MECHANIC';
            tutorialPhaseLabel.textContent = afterLabel;
            tutorialPhaseLabel.className   = 'tutorial-phase-label phase-after';
        }

        const handChanges  = (this.cardChanges && this.cardChanges.handChanges)  || {};
        const scoreChanges = (this.cardChanges && this.cardChanges.scoreChanges) || {};

        for (const p of [1, 2]) {
            const handEl  = document.getElementById(`tut-hand-${p}`);
            const scoreEl = document.getElementById(`tut-score-${p}`);
            if (!handEl || !scoreEl) continue;

            const hand  = (state.hands  || {})[p] || (state.hands  || {})[String(p)] || [];
            const score = (state.scores || {})[p] || (state.scores || {})[String(p)] || 0;

            handEl.innerHTML = '';
            hand.forEach(val => {
                const chip = document.createElement('span');
                chip.className   = 'tut-card-chip';
                chip.textContent = val;
                handEl.appendChild(chip);
            });
            scoreEl.textContent = score;

            const handBlock  = handEl;
            const scoreBlock = scoreEl.parentElement;
            handBlock.classList.remove('mechanic-changed');
            scoreBlock.classList.remove('mechanic-changed');
            if (this.phase === 'after') {
                if (handChanges[p])  handBlock.classList.add('mechanic-changed');
                if (scoreChanges[p]) scoreBlock.classList.add('mechanic-changed');
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

    // DOM refs for the library view. The grid is split into two columns
    // (board / card) and each card routes to the column matching its
    // game_type. _gridEl(card) picks the right one; falls back to board
    // for cards missing a game_type field (older saves).
    get _emptyEl()  { return document.getElementById('library-empty'); },
    _gridEl(card) {
        const gt = (card && card.game_type) === 'card' ? 'card' : 'board';
        return document.getElementById(`library-grid-${gt}`);
    },
    _countEl(card) {
        const gt = (card && card.game_type) === 'card' ? 'card' : 'board';
        return document.getElementById(`library-count-${gt}`);
    },

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
        const gridEl = this._gridEl(card);
        if (gridEl) gridEl.appendChild(el);
        const countEl = this._countEl(card);
        if (countEl) {
            const n = parseInt(countEl.textContent, 10) || 0;
            countEl.textContent = n + 1;
        }
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

        // Dispatch by game type so card-game cards get a hand+score
        // tutorial instead of the empty 6x6 board they used to render.
        if (card.game_type === 'card') {
            this._startCardAnimation(id, cardEl, card);
            return;
        }

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

    // Card-game version of _startAnimation. Same loop structure (BEFORE/
    // AFTER fade) but renders two hand+score blocks instead of a 6x6 grid.
    // Highlights changed hands/scores with the same purple pulse.
    _startCardAnimation(id, cardEl, card) {
        const trigger = detectCardMechanicTrigger(card.replay.moves);
        const gridEl      = cardEl.querySelector('.lib-tutorial-grid');
        const phaseLabelEl = cardEl.querySelector('.lib-phase-label');

        if (!trigger) {
            gridEl.innerHTML = '<div class="lib-no-trigger">Not triggered in this replay</div>';
            return;
        }

        gridEl.classList.add('card-layout');
        gridEl.innerHTML = `
            <div class="tut-card-side">
                <div class="tut-card-label">Player 1</div>
                <div class="tut-card-hand" data-hand="1"></div>
                <div class="tut-card-score">Score: <span data-score="1">0</span></div>
            </div>
            <div class="tut-card-vs">vs</div>
            <div class="tut-card-side">
                <div class="tut-card-label">Player 2</div>
                <div class="tut-card-hand" data-hand="2"></div>
                <div class="tut-card-score">Score: <span data-score="2">0</span></div>
            </div>`;

        const anim = { generation: 0, timeout: null, phase: 'before',
                       BEFORE_MS: 2000, AFTER_MS: 2800 };
        this._animations[id] = anim;

        const renderPhase = () => {
            const state = anim.phase === 'before' ? trigger.before : trigger.after;
            if (!state) return;

            if (anim.phase === 'before') {
                phaseLabelEl.textContent = 'BEFORE';
                phaseLabelEl.className   = 'lib-phase-label phase-before';
            } else {
                const labels = {
                    card:              'AFTER MECHANIC',
                    card_extra_turn:   'EXTRA TURN',
                    card_custom_state: 'STATE UPDATED',
                };
                phaseLabelEl.textContent = labels[trigger.type] || 'AFTER MECHANIC';
                phaseLabelEl.className   = 'lib-phase-label phase-after';
            }

            const handChanges  = trigger.handChanges  || {};
            const scoreChanges = trigger.scoreChanges || {};
            for (const p of [1, 2]) {
                const handEl  = gridEl.querySelector(`[data-hand="${p}"]`);
                const scoreEl = gridEl.querySelector(`[data-score="${p}"]`);
                if (!handEl || !scoreEl) continue;

                const hand  = (state.hands  || {})[p] || (state.hands  || {})[String(p)] || [];
                const score = (state.scores || {})[p] || (state.scores || {})[String(p)] || 0;

                handEl.innerHTML = '';
                hand.forEach(val => {
                    const chip = document.createElement('span');
                    chip.className   = 'tut-card-chip';
                    chip.textContent = val;
                    handEl.appendChild(chip);
                });
                scoreEl.textContent = score;

                const handBlock  = handEl;
                const scoreBlock = scoreEl.parentElement;
                handBlock.classList.remove('mechanic-changed');
                scoreBlock.classList.remove('mechanic-changed');
                if (anim.phase === 'after') {
                    if (handChanges[p])  handBlock.classList.add('mechanic-changed');
                    if (scoreChanges[p]) scoreBlock.classList.add('mechanic-changed');
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
const aivaiView    = document.getElementById('aivai-view');
const pairlabView  = document.getElementById('pairlab-view');
const tabPipeline  = document.getElementById('tab-pipeline');
const tabLibrary   = document.getElementById('tab-library');
const tabAivai     = document.getElementById('tab-aivai');
const tabPairlab   = document.getElementById('tab-pairlab');

function switchTab(tab) {
    if (tab !== 'aivai' && typeof aivaiManager !== 'undefined') {
        aivaiManager.pause();
    }

    // Persist so a refresh stays on the same tab.
    try { localStorage.setItem('dv-active-tab', tab); } catch (e) { /* private mode */ }

    // Hide everything first then show the active one
    mainLayout.classList.add('hidden');
    libraryView.classList.add('hidden');
    aivaiView.classList.add('hidden');
    pairlabView.classList.add('hidden');
    tabPipeline.classList.remove('active');
    tabLibrary.classList.remove('active');
    tabAivai.classList.remove('active');
    tabPairlab.classList.remove('active');

    if (tab === 'pipeline') {
        mainLayout.classList.remove('hidden');
        tabPipeline.classList.add('active');
        if (libraryManager.expandedId !== null) {
            libraryManager._collapseCard(libraryManager.expandedId);
            libraryManager.expandedId = null;
        }
    } else if (tab === 'library') {
        libraryView.classList.remove('hidden');
        tabLibrary.classList.add('active');
    } else if (tab === 'aivai') {
        aivaiView.classList.remove('hidden');
        tabAivai.classList.add('active');
        aivaiManager.onTabOpened();
        if (typeof playMeManager !== 'undefined') {
            playMeManager.onTabOpened();
        }
    } else if (tab === 'pairlab') {
        pairlabView.classList.remove('hidden');
        tabPairlab.classList.add('active');
    }
}

tabPipeline.addEventListener('click', () => switchTab('pipeline'));
tabLibrary.addEventListener('click',  () => switchTab('library'));
tabAivai.addEventListener('click',    () => switchTab('aivai'));
tabPairlab.addEventListener('click',  () => switchTab('pairlab'));


// ── AI vs AI tab ─────────────────────────────────────────────────────────────
//
// Loads the top 2 card mechanics from the backend, runs one MCTS-vs-MCTS
// match on demand, and animates the result move by move with clear
// attribution of which mechanic fired.

const AIVAI_LOADOUT_KEY = 'dv-aivai-loadout';

const aivaiManager = {
    loadout:           null,
    match:             null,
    moveIdx:           0,
    playing:           false,
    timer:             null,
    mechColorClass:    {},

    init() {
        this.newBtn         = document.getElementById('aivai-new-btn');
        this.replayBtn      = document.getElementById('aivai-replay-btn');
        this.playBtn        = document.getElementById('aivai-play-btn');
        this.backBtn        = document.getElementById('aivai-back-btn');
        this.forwardBtn     = document.getElementById('aivai-forward-btn');
        this.speedEl        = document.getElementById('aivai-speed');
        this.statusEl       = document.getElementById('aivai-status');
        this.boardEl        = document.getElementById('aivai-board');
        this.resultEl       = document.getElementById('aivai-result');
        this.bannerEl       = document.getElementById('aivai-mech-banner');
        this.turnNum        = document.getElementById('aivai-turn-num');
        this.turnTotal      = document.getElementById('aivai-turn-total');
        this.loadoutCardsEl = document.getElementById('aivai-loadout-cards');

        this.newBtn.addEventListener('click',     () => this.startNewGame());
        this.replayBtn.addEventListener('click',  () => this.replay());
        this.playBtn.addEventListener('click',    () => this.togglePlay());
        this.backBtn.addEventListener('click',    () => this.stepBack());
        this.forwardBtn.addEventListener('click', () => this.stepForward());

        this._loadoutFetched = false;
    },

    onTabOpened() {
        if (!this._loadoutFetched) {
            this._loadoutFetched = true;
            // Prefer a previously-saved loadout (e.g., one launched from Pair
            // Lab) so a page refresh doesn't snap the user back to default
            // top-2. Fall back to fetching defaults if no saved loadout.
            if (!this._restoreSavedLoadout()) {
                this.fetchLoadout();
            }
        }
    },

    _restoreSavedLoadout() {
        try {
            const raw = localStorage.getItem(AIVAI_LOADOUT_KEY);
            if (!raw) return false;
            const saved = JSON.parse(raw);
            if (!Array.isArray(saved) || saved.length === 0) return false;
            this.loadout = saved;
            this.mechColorClass = {};
            saved.forEach((m, i) => {
                this.mechColorClass[m.name] = `mech-${i + 1}`;
            });
            this._renderLoadout();
            return true;
        } catch (e) { return false; }
    },

    _saveCustomLoadout(loadout) {
        // Only the small JSON-safe fields the UI needs for chips.
        try {
            const minimal = (loadout || []).map(m => ({
                name:        m.name,
                description: m.description,
                aggregate:   m.aggregate,
                patched:     !!m.patched,
            }));
            localStorage.setItem(AIVAI_LOADOUT_KEY, JSON.stringify(minimal));
        } catch (e) { /* quota / private mode — silent fail */ }
    },

    async fetchLoadout() {
        try {
            const resp = await fetch('/api/aivai/loadout');
            const data = await resp.json();
            this.loadout = data;
            this.mechColorClass = {};
            data.forEach((m, i) => {
                this.mechColorClass[m.name] = `mech-${i + 1}`;
            });
            this._renderLoadout();
            // Persist the default top-2 too, so a refresh stays consistent.
            // If the underlying library later changes, clearing the library
            // (Reset Library button) wipes this cache via aivaiManager.clearSavedLoadout.
            this._saveCustomLoadout(data);
        } catch (e) {
            this.loadoutCardsEl.innerHTML =
                `<span class="dim">Failed to load mechanics: ${e}</span>`;
        }
    },

    clearSavedLoadout() {
        try { localStorage.removeItem(AIVAI_LOADOUT_KEY); } catch (e) { /* ignore */ }
    },

    _renderLoadout() {
        if (!this.loadout || this.loadout.length === 0) {
            this.loadoutCardsEl.innerHTML =
                '<span class="dim">No card mechanics in the library yet.</span>';
            this.newBtn.disabled = true;
            return;
        }
        this.loadoutCardsEl.innerHTML = '';
        this.loadout.forEach((m, i) => {
            const chip = document.createElement('div');
            chip.className = `aivai-mech-chip mech-${i + 1}` +
                             (m.patched ? ' patched' : '');
            chip.title = m.patched
                ? '(showcase patch applied to fix LLM-hallucinated double-counting)'
                : '';
            chip.innerHTML =
                `<div class="aivai-mech-chip-head">` +
                    `<span class="dot"></span>` +
                    `<span class="aivai-mech-chip-name">${escapeHtml(m.name)}</span>` +
                    `<span class="aivai-mech-chip-agg">agg ${m.aggregate.toFixed(2)}</span>` +
                `</div>` +
                `<div class="aivai-mech-chip-desc">${escapeHtml(m.description || 'No description provided.')}</div>`;
            this.loadoutCardsEl.appendChild(chip);
        });
        this.newBtn.disabled = false;
    },

    async startNewGame() {
        // Use whichever loadout is currently displayed so a restored custom
        // loadout sticks across "New Game" clicks. If the displayed loadout
        // is the default top-2 (loaded via fetchLoadout), names will match
        // the backend default and the result is identical.
        const names = (this.loadout || []).map(m => m.name).filter(Boolean);
        return this._runMatch(names.length > 0 ? names : null);
    },

    async startNewGameWithLoadout(mechanicNames) {
        // Used when the Pair Lab tab launches a specific combo. Updates the
        // loadout strip immediately to those names so the user sees the
        // change before the match finishes.
        this._previewLoadout(mechanicNames);
        return this._runMatch(mechanicNames);
    },

    async _runMatch(mechanicNames) {
        this.pause();
        this.match = null;
        this.moveIdx = 0;
        this.boardEl.classList.add('hidden');
        this.resultEl.classList.add('hidden');
        this.replayBtn.disabled = true;
        this.playBtn.disabled = true;
        this.newBtn.disabled = true;
        this._updateNavButtons();   // disables ← / → while no match is loaded
        const label = mechanicNames && mechanicNames.length
            ? `Running match with ${mechanicNames.join(' + ')}... (minimax depth 8)`
            : 'Running match (minimax depth 8, ~1-2s per move)...';
        this.statusEl.textContent = label;

        const body = { simulations: 200 };
        if (mechanicNames && mechanicNames.length) body.mechanic_names = mechanicNames;

        try {
            const resp = await fetch('/api/aivai/match', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(body),
            });
            if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
            const data = await resp.json();
            this.match = data;
            // Sync loadout strip + color map with what the backend actually used
            this.loadout = data.loadout || [];
            this.mechColorClass = {};
            this.loadout.forEach((m, i) => {
                this.mechColorClass[m.name] = `mech-${i + 1}`;
            });
            this._renderLoadout();
            // Persist whichever loadout the backend actually played with so
            // a refresh restores the same chips (and "New Game" replays it).
            this._saveCustomLoadout(this.loadout);
            this._beginPlayback();
        } catch (e) {
            this.statusEl.textContent = `Match failed: ${e}`;
            this.newBtn.disabled = false;
        }
    },

    _previewLoadout(mechanicNames) {
        // Render placeholder chips immediately so the user can see the new
        // loadout while the backend is working. Real chips replace these
        // when the match returns.
        if (!this.loadoutCardsEl) return;
        this.loadoutCardsEl.innerHTML = '';
        mechanicNames.forEach((name, i) => {
            const chip = document.createElement('div');
            chip.className = `aivai-mech-chip mech-${i + 1}`;
            chip.innerHTML =
                `<div class="aivai-mech-chip-head">` +
                    `<span class="dot"></span>` +
                    `<span class="aivai-mech-chip-name">${escapeHtml(name)}</span>` +
                    `<span class="aivai-mech-chip-agg">loading...</span>` +
                `</div>` +
                `<div class="aivai-mech-chip-desc dim">Fetching mechanic rules...</div>`;
            this.loadoutCardsEl.appendChild(chip);
        });
    },

    replay() {
        if (!this.match) return;
        this.pause();
        this.moveIdx = 0;
        this._beginPlayback();
    },

    _beginPlayback() {
        this.boardEl.classList.remove('hidden');
        this.resultEl.classList.add('hidden');
        this.turnTotal.textContent = this.match.moves.length;
        this.turnNum.textContent   = 0;
        this.newBtn.disabled    = false;
        this.replayBtn.disabled = false;
        this.playBtn.disabled   = false;
        this.statusEl.textContent = 'Playing back...';

        if (this.match.moves.length > 0) {
            this._renderInitialState(this.match.moves[0].before_move);
        }
        this._clearBanner();
        this._updateNavButtons();
        this.play();
    },

    play() {
        if (!this.match) return;
        if (this.moveIdx >= this.match.moves.length) {
            this._showResult();
            return;
        }
        this.playing = true;
        this.playBtn.innerHTML = '&#10074;&#10074;';
        this._scheduleNextMove();
    },

    pause() {
        this.playing = false;
        if (this.timer) {
            clearTimeout(this.timer);
            this.timer = null;
        }
        if (this.playBtn) this.playBtn.innerHTML = '&#9654;';
    },

    togglePlay() {
        if (this.playing) this.pause();
        else this.play();
    },

    _scheduleNextMove() {
        if (!this.playing) return;
        // Slider semantics: pulling right = faster. The HTML input still
        // ranges from 300 to 2500, but we invert it here so the actual
        // delay between moves DECREASES as the slider value INCREASES,
        // matching user intuition (fuller bar = more speed).
        const min   = parseInt(this.speedEl.min, 10)   || 300;
        const max   = parseInt(this.speedEl.max, 10)   || 2500;
        const value = parseInt(this.speedEl.value, 10) || 1100;
        const delay = max + min - value;
        this.timer = setTimeout(() => this._stepOnce(), delay);
    },

    _stepOnce() {
        if (!this.match || this.moveIdx >= this.match.moves.length) {
            this._showResult();
            this.pause();
            return;
        }
        const move = this.match.moves[this.moveIdx];
        this._renderMove(move);
        this.moveIdx += 1;
        this.turnNum.textContent = this.moveIdx;
        this._updateNavButtons();
        if (this.moveIdx >= this.match.moves.length) {
            this.timer = setTimeout(() => {
                this._showResult();
                this.pause();
            }, 1200);
        } else {
            this._scheduleNextMove();
        }
    },

    // ── Manual stepping ────────────────────────────────────────────────────
    //
    // Pauses auto-playback and renders one move at a time. Same UX as the
    // Play vs AI panel: ← rewinds one move, → advances one move. moveIdx
    // semantics: it points at the NEXT move to render. moveIdx === 0 means
    // the initial state is showing; moveIdx === moves.length means the
    // game is fully played out.

    stepForward() {
        if (!this.match) return;
        if (this.moveIdx >= this.match.moves.length) return;
        this.pause();
        const move = this.match.moves[this.moveIdx];
        this._renderMove(move, { animate: false });
        this.moveIdx += 1;
        this.turnNum.textContent = this.moveIdx;
        if (this.moveIdx >= this.match.moves.length) {
            this._showResult();
        } else {
            this.resultEl.classList.add('hidden');
        }
        this._updateNavButtons();
    },

    stepBack() {
        if (!this.match) return;
        if (this.moveIdx <= 0) return;
        this.pause();
        this.moveIdx -= 1;
        if (this.moveIdx === 0) {
            // Rolled back past the first move — show initial state.
            this._renderInitialState(this.match.moves[0].before_move);
            this.turnNum.textContent = 0;
            this._clearBanner();
        } else {
            // moveIdx now points at the next-to-render move; the visible
            // state is the result of moves[moveIdx - 1].
            this._renderMove(this.match.moves[this.moveIdx - 1], { animate: false });
            this.turnNum.textContent = this.moveIdx;
        }
        this.resultEl.classList.add('hidden');
        this._updateNavButtons();
    },

    _updateNavButtons() {
        if (!this.backBtn || !this.forwardBtn) return;
        if (!this.match) {
            this.backBtn.disabled    = true;
            this.forwardBtn.disabled = true;
            return;
        }
        this.backBtn.disabled    = (this.moveIdx <= 0);
        this.forwardBtn.disabled = (this.moveIdx >= this.match.moves.length);
    },

    _renderInitialState(state) {
        this._renderHands(state);
        this._renderScores(state, null);
        this._renderPlayed(1, null, false);
        this._renderPlayed(2, null, false);
        this._setActiveTurn(state.current_player);
        this._clearMechanicAffected();
    },

    _renderMove(move, options = {}) {
        // options.animate : default true. When false, snap-render with no
        //                   card-fly animation (used by step-back / step-forward
        //                   so scrubbing through history feels instant).
        const animate  = options.animate !== false;
        const before   = move.before_move;
        const afterRaw = move.after_raw_move;

        this._setActiveTurn(move.player);

        // FLIP step 1: read the rect of the card that's about to leave the
        // hand BEFORE we re-render, so we know where to fly from.
        let sourceRect = null;
        if (animate && typeof move.card_index === 'number'
                    && move.card_index >= 0
                    && move.card_played != null) {
            sourceRect = this._captureCardRect(move.player, move.card_index);
        }

        this._renderHands(afterRaw);
        this._renderScores(afterRaw, before);
        this._renderPlayed(move.player, move.card_played, true);
        this._renderPlayed(move.player === 1 ? 2 : 1, null, false);

        // FLIP step 2-4: position the new "Just played" card visually at
        // the hand source then transition it back to identity.
        if (sourceRect) {
            this._flyCardFromSourceTo(move.player, sourceRect);
        }

        const fired = move.mechanics.filter(m => m.fired);
        if (fired.length === 0) {
            this._clearBanner();
            this._clearMechanicAffected();
        } else {
            this._showMechanicBanner(fired);
            const finalState = move.mechanics[move.mechanics.length - 1].after;
            this._renderScores(finalState, before);
            this._renderHands(finalState);
            this._highlightAffectedPlayers(fired);
        }
    },

    // FLIP helper: read the bounding rect of the card about to leave the hand.
    _captureCardRect(player, cardIndex) {
        const handEl = document.getElementById(`aivai-hand-${player}`);
        if (!handEl) return null;
        const cards = handEl.querySelectorAll('.aivai-card');
        if (cardIndex < 0 || cardIndex >= cards.length) return null;
        return cards[cardIndex].getBoundingClientRect();
    },

    // FLIP helper: invert the new "Just played" card to the source position
    // (no transition), force a layout flush, then transition back to identity
    // so the card visibly flies + scales from the hand into its played slot.
    _flyCardFromSourceTo(player, sourceRect) {
        const slot = document.getElementById(`aivai-played-${player}`);
        if (!slot) return;
        const cardEl = slot.querySelector('.aivai-card');
        if (!cardEl) return;

        const destRect = cardEl.getBoundingClientRect();
        const dx = sourceRect.left - destRect.left;
        const dy = sourceRect.top  - destRect.top;
        // Hand cards are smaller than the just-played card, so start at the
        // source size and scale up to make it look like the card is growing
        // as it lands.
        const scaleStart = destRect.width
            ? Math.max(0.2, sourceRect.width / destRect.width)
            : 0.85;

        cardEl.style.transition       = 'none';
        cardEl.style.transformOrigin  = 'top left';
        cardEl.style.transform        = `translate(${dx}px, ${dy}px) scale(${scaleStart})`;
        cardEl.style.opacity          = '0.92';
        cardEl.style.zIndex           = '5';

        void cardEl.offsetWidth;   // force layout so the inverse state commits

        cardEl.style.transition =
            'transform 0.34s cubic-bezier(0.2, 0.75, 0.3, 1), opacity 0.28s ease';
        cardEl.style.transform = 'translate(0, 0) scale(1)';
        cardEl.style.opacity   = '1';

        // Clear inline styles after the transition completes so subsequent
        // renders don't inherit them.
        setTimeout(() => {
            cardEl.style.transition      = '';
            cardEl.style.transform       = '';
            cardEl.style.transformOrigin = '';
            cardEl.style.opacity         = '';
            cardEl.style.zIndex          = '';
        }, 380);
    },

    _renderHands(state) {
        for (const p of [1, 2]) {
            const handEl = document.getElementById(`aivai-hand-${p}`);
            if (!handEl) continue;
            const hand = (state.hands || {})[p] || (state.hands || {})[String(p)] || [];
            handEl.innerHTML = '';
            hand.forEach(val => {
                const card = document.createElement('span');
                card.className = 'aivai-card';
                card.textContent = val;
                handEl.appendChild(card);
            });
        }
    },

    _renderScores(state, prev) {
        for (const p of [1, 2]) {
            const scoreEl = document.getElementById(`aivai-score-${p}`);
            if (!scoreEl) continue;
            const newVal = (state.scores || {})[p] ?? (state.scores || {})[String(p)] ?? 0;
            const oldVal = prev
                ? ((prev.scores || {})[p] ?? (prev.scores || {})[String(p)] ?? 0)
                : newVal;
            scoreEl.textContent = newVal;
            scoreEl.classList.remove('flash-up', 'flash-down', 'flash-reset');
            if (newVal > oldVal) {
                scoreEl.classList.add('flash-up');
            } else if (newVal === 0 && oldVal > 0) {
                scoreEl.classList.add('flash-reset');
            } else if (newVal < oldVal) {
                scoreEl.classList.add('flash-down');
            }
        }
    },

    _renderPlayed(player, cardValue, justPlayed) {
        const slot = document.getElementById(`aivai-played-${player}`);
        if (!slot) return;
        if (cardValue == null) {
            slot.innerHTML = '<span class="dim">&mdash;</span>';
            return;
        }
        slot.innerHTML = '';
        const card = document.createElement('span');
        card.className = 'aivai-card' + (justPlayed ? ' just-played' : '');
        card.textContent = cardValue;
        slot.appendChild(card);
    },

    _setActiveTurn(player) {
        document.getElementById('aivai-player-1').classList.toggle('active-turn', player === 1);
        document.getElementById('aivai-player-2').classList.toggle('active-turn', player === 2);
    },

    _showMechanicBanner(firedList) {
        this.bannerEl.className = 'aivai-mech-banner fired';
        if (firedList.length === 1) {
            const cls = this.mechColorClass[firedList[0].name] || 'mech-1';
            this.bannerEl.classList.add(cls);
        } else {
            // Multi-fire: 'mixed' = neutral banner so each per-name color
            // wins individually instead of being washed purple by the
            // parent-rooted .aivai-mech-banner.mech-1 .aivai-mech-banner-name
            // rule.
            this.bannerEl.classList.add('mixed');
        }

        const lines = firedList.map(f => this._describeFire(f));
        this.bannerEl.innerHTML = lines.map((html, i) =>
            (i > 0 ? '<div class="aivai-mech-banner-effect both-fired">' : '<div>') +
            html + '</div>'
        ).join('');
    },

    _describeFire(firedEvent) {
        const cls = this.mechColorClass[firedEvent.name] || 'mech-1';
        const parts = [];
        const sc = firedEvent.score_changes || {};
        for (const p of ['1', '2']) {
            if (sc[p]) {
                const delta = sc[p].after - sc[p].before;
                if (sc[p].after === 0 && sc[p].before > 0) {
                    parts.push(`P${p} score reset to 0 (was ${sc[p].before})`);
                } else if (delta > 0) {
                    parts.push(`P${p} +${delta} -> ${sc[p].after}`);
                } else if (delta < 0) {
                    parts.push(`P${p} ${delta} -> ${sc[p].after}`);
                }
            }
        }
        const hc = firedEvent.hand_changes || {};
        for (const p of ['1', '2']) {
            if (hc[p]) parts.push(`P${p} hand changed`);
        }
        if (firedEvent.extra_turn_changed) parts.push('Extra turn granted');

        const effect = parts.join('. ') || 'Effect applied';
        // Carry both the legacy `${cls}-name` (no CSS, kept for safety) and
        // the new direct `${cls}` class so the multi-fire case picks up the
        // per-name color via .aivai-mech-banner-name.mech-1 / .mech-2.
        return (
            `<div class="aivai-mech-banner-name ${cls}-name ${cls}">${firedEvent.name}</div>` +
            `<div class="aivai-mech-banner-effect">${effect}</div>`
        );
    },

    _highlightAffectedPlayers(firedList) {
        this._clearMechanicAffected();
        for (const f of firedList) {
            const cls = this.mechColorClass[f.name] || 'mech-1';
            const sc = f.score_changes || {};
            const hc = f.hand_changes  || {};
            for (const p of ['1', '2']) {
                if (sc[p] || hc[p]) {
                    document.getElementById(`aivai-player-${p}`)
                        .classList.add('mechanic-affected', cls);
                }
            }
        }
    },

    _clearMechanicAffected() {
        for (const p of [1, 2]) {
            const el = document.getElementById(`aivai-player-${p}`);
            el.classList.remove('mechanic-affected', 'mech-1', 'mech-2');
        }
    },

    _clearBanner() {
        this.bannerEl.className = 'aivai-mech-banner';
        this.bannerEl.innerHTML = '<span class="dim">No mechanic fired yet</span>';
    },

    _showResult() {
        if (!this.match) return;
        const w   = this.match.winner;
        const fs  = this.match.final_scores;
        const cap = this.match.hit_safety_cap;

        let title;
        if (w === 1 || w === 2) {
            title = `Player ${w} wins`;
            this.resultEl.className = `aivai-result winner-${w}`;
        } else {
            const s1 = fs['1'], s2 = fs['2'];
            if (s1 > s2) {
                title = 'Player 1 leads (no winner reached 45)';
                this.resultEl.className = 'aivai-result winner-1';
            } else if (s2 > s1) {
                title = 'Player 2 leads (no winner reached 45)';
                this.resultEl.className = 'aivai-result winner-2';
            } else {
                title = 'Draw';
                this.resultEl.className = 'aivai-result draw';
            }
        }
        const detail =
            `Final scores  P1 ${fs['1']}  &middot;  P2 ${fs['2']}` +
            `  &middot;  ${this.match.total_turns} turns` +
            (cap ? '  &middot;  hit safety cap' : '');
        this.resultEl.innerHTML =
            `<div class="aivai-result-title">${title}</div>` +
            `<div class="aivai-result-detail">${detail}</div>`;
        this.resultEl.classList.remove('hidden');
        this.statusEl.textContent = 'Done';
    },
};


// ── Play vs AI (lives inside the AI vs AI tab, below the showcase) ─────────
//
// Lets the human play Player 1 against an MCTS Player 2 with the same loadout.
// Server keeps the active game in memory; the client just sends card_index
// and animates the returned event list (human's move plus any AI responses).

const playMeManager = {
    sims:        200,
    loadout:     [],
    mechColorClass: {},
    state:       null,
    legalMoves:  [],
    finished:    false,
    busy:        false,
    animTimers:  [],

    init() {
        this.newBtn      = document.getElementById('playme-new-btn');
        this.agentEl     = document.getElementById('playme-agent');
        this.simsEl      = document.getElementById('playme-sims');
        this.simsLabel   = document.getElementById('playme-sims-label');
        this.depthEl     = document.getElementById('playme-depth');
        this.depthLabel  = document.getElementById('playme-depth-label');
        this.statusEl    = document.getElementById('playme-status');
        this.boardEl     = document.getElementById('playme-board');
        this.bannerEl    = document.getElementById('playme-mech-banner');
        this.turnNum     = document.getElementById('playme-turn-num');
        this.turnTotal   = document.getElementById('playme-turn-total');
        this.resultEl    = document.getElementById('playme-result');
        this.backBtn     = document.getElementById('playme-back-btn');
        this.forwardBtn  = document.getElementById('playme-forward-btn');

        this.newBtn.addEventListener('click',     () => this.startNewGame());
        this.backBtn.addEventListener('click',    () => this.stepBack());
        this.forwardBtn.addEventListener('click', () => this.stepForward());
        this.agentEl.addEventListener('change',   () => this._onAgentTypeChange());

        this._statusFetched = false;

        // History scrubbing state.
        // _history accumulates one entry per move (P1 or P2). _viewIdx ranges
        // [0..history.length]: 0 = initial state, K = state after event K-1,
        // history.length = "live" (interactive). null = no game yet.
        this._history       = [];
        this._initialState  = null;
        this._viewIdx       = null;
    },

    onTabOpened() {
        // Fetch any existing session once when the tab is first opened so the
        // user can resume a game across tab switches without losing state.
        if (this._statusFetched) return;
        this._statusFetched = true;
        this._fetchStatus();
    },

    async _fetchStatus() {
        try {
            const resp = await fetch('/api/play/status');
            const data = await resp.json();
            if (!data.active) return;
            this._adoptSession(data);
            this.statusEl.textContent = data.finished
                ? 'Game over'
                : (data.state.current_player === 1 ? 'Your turn' : 'AI thinking...');
            if (data.finished) this._showResult();
        } catch (e) { /* server might not be up */ }
    },

    async startNewGame() {
        this._cancelAnimations();
        this.busy = true;
        this.newBtn.disabled = true;
        this.boardEl.classList.add('hidden');
        this.resultEl.classList.add('hidden');
        this.statusEl.textContent = 'Starting...';

        const agentType = this.agentEl.value || 'mcts';
        const sims  = Math.max(1, parseInt(this.simsEl.value, 10)  || 200);
        const depth = Math.max(2, parseInt(this.depthEl.value, 10) || 8);
        this.sims      = sims;
        this.depth     = depth;
        this.agentType = agentType;

        // Use whatever loadout the AI vs AI showcase is currently displaying
        // so the chip strip above and the Play vs AI session match. Without
        // this, Play vs AI silently fell back to the backend's default top-2,
        // which diverges from the showcase the moment a custom loadout is in
        // play (e.g. one launched from the Pair Lab).
        const loadoutNames = (typeof aivaiManager !== 'undefined' && aivaiManager.loadout)
            ? aivaiManager.loadout.map(m => m.name).filter(Boolean)
            : [];

        try {
            const resp = await fetch('/api/play/new', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({
                    agent_type:     agentType,
                    simulations:    sims,
                    depth:          depth,
                    mechanic_names: loadoutNames,
                }),
            });
            if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
            const data = await resp.json();
            this._adoptSession(data);
            this.statusEl.textContent = 'Your turn';
        } catch (e) {
            this.statusEl.textContent = `Failed to start: ${e}`;
        } finally {
            this.newBtn.disabled = false;
            this.busy = false;
            this._updateNavButtons();
        }
    },

    _onAgentTypeChange() {
        const isMinimax = (this.agentEl.value === 'minimax');
        this.simsLabel.classList.toggle('hidden',  isMinimax);
        this.depthLabel.classList.toggle('hidden', !isMinimax);
    },

    _aiBudgetLabel() {
        return this.agentType === 'minimax'
            ? `at depth ${this.depth}`
            : `at ${this.sims} sims`;
    },

    _aiThinkingLabel() {
        return this.agentType === 'minimax'
            ? `AI thinking (minimax depth ${this.depth})...`
            : `AI thinking (${this.sims} sims)...`;
    },

    _adoptSession(data) {
        this.loadout      = data.loadout || [];
        this.state        = data.state;
        this.legalMoves   = data.legal_moves || [];
        this.finished     = !!data.finished;
        this.agentType    = data.agent_type || 'mcts';
        this.sims         = data.simulations || 200;
        this.depth        = data.depth || 8;
        this.handTriggers = data.hand_triggers || [];

        // Reflect the active agent settings in the UI so a refresh resumes
        // with the right dropdown + input visible.
        if (this.agentEl)  this.agentEl.value  = this.agentType;
        if (this.simsEl)   this.simsEl.value   = this.sims;
        if (this.depthEl)  this.depthEl.value  = this.depth;
        this._onAgentTypeChange();

        // Match aivaiManager's color convention so players see consistent colors
        // across the showcase and Play vs AI panels.
        this.mechColorClass = {};
        this.loadout.forEach((m, i) => {
            this.mechColorClass[m.name] = `mech-${i + 1}`;
        });

        // Reset history scrubbing for the new game.
        this._history      = [];
        this._initialState = JSON.parse(JSON.stringify(data.state));
        this._viewIdx      = 0;

        this.boardEl.classList.remove('hidden');
        this.resultEl.classList.add('hidden');
        this.turnTotal.textContent = 0;
        this.turnNum.textContent   = 0;

        this._clearMechanicAffected();
        this._clearBanner();
        this._renderState(this.state, null, /*clickable=*/!this.finished);
        this._updateNavButtons();
        this._maybeAutoPass();
    },

    _maybeAutoPass() {
        // If the game isn't over and it's the human's turn but their hand is
        // empty, the only legal move is -1 (pass). Auto-submit it after a
        // brief moment so the AI can keep playing out its remaining cards.
        if (this.busy || this.finished || !this.state) return;
        if (this.state.current_player !== 1) return;
        const hand = (this.state.hands || {})[1] || (this.state.hands || {})['1'] || [];
        if (hand.length > 0) return;
        if (!this.legalMoves.includes(-1)) return;
        this.statusEl.textContent = 'No cards left, passing...';
        this.animTimers.push(setTimeout(() => {
            this.onCardClick(-1);
        }, 700));
    },

    async onCardClick(cardIndex) {
        if (this.busy || this.finished) return;
        if (!this.state || this.state.current_player !== 1) return;
        if (!this.legalMoves.includes(cardIndex)) return;

        this.busy = true;
        this._setHandClickable(false);
        this._updateNavButtons();
        this.statusEl.textContent = this._aiThinkingLabel();
        const t0 = performance.now();

        // Fire the optimistic local card-fly NOW so the player gets instant
        // visual feedback. Mechanics + AI response come from the server when
        // the parallel fetch resolves.
        const beforeState = this.state;
        const didLocalRender = this._renderLocalRawMove(beforeState, cardIndex);

        try {
            const resp = await fetch('/api/play/move', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ card_index: cardIndex }),
            });
            const data = await resp.json();
            if (!resp.ok || (data.error && !data.events)) {
                this.statusEl.textContent = `Error: ${data.error || resp.status}`;
                this.busy = false;
                this._setHandClickable(true);
                this._updateNavButtons();
                return;
            }

            // Animate the events one at a time so the user can see what fired.
            // If we already showed the human's raw card-fly locally, tell
            // _playEvents to skip that part of event[0] and just play the
            // mechanic phase + the AI's response.
            const newEvents = data.events || [];
            this._playEvents(newEvents, didLocalRender, () => {
                this.state        = data.state;
                this.legalMoves   = data.legal_moves || [];
                this.finished     = !!data.finished;
                this.handTriggers = data.hand_triggers || [];
                this.busy = false;

                // Append to history and snap the view cursor to "live".
                this._history.push(...newEvents);
                this._viewIdx = this._history.length;
                this.turnTotal.textContent = this._history.length;
                this.turnNum.textContent   = this._history.length;
                this._updateNavButtons();

                // Move the active-turn highlight off whichever player just
                // finished animating and onto whoever's turn it actually is
                // now. _renderEvent leaves the highlight on the most recent
                // actor, which is wrong once the AI has finished playing.
                this._setActiveTurn(this.finished ? 0 : this.state.current_player);

                const elapsedMs = Math.round(performance.now() - t0);
                if (this.finished) {
                    this._showResult();
                    this.statusEl.textContent = `Game over (AI took ${elapsedMs}ms)`;
                } else if (this.state.current_player === 1) {
                    this.statusEl.textContent =
                        `Your turn (AI took ${elapsedMs}ms ${this._aiBudgetLabel()})`;
                    this._setHandClickable(true);
                    this._maybeAutoPass();
                } else {
                    this.statusEl.textContent = this._aiThinkingLabel();
                }
            });
        } catch (e) {
            this.statusEl.textContent = `Move failed: ${e}`;
            this.busy = false;
            this._setHandClickable(true);
            this._updateNavButtons();
        }
    },

    _playEvents(events, skipFirstRaw, onDone) {
        // Walk through each event with a delay between them so mechanic
        // firings get a moment to register visually. Banner is cleared when
        // a move had no firings so a non-firing turn does not look stale.
        //
        // skipFirstRaw=true means event[0]'s card-fly was already rendered
        // locally on click (optimistic render). We render only its mechanic
        // phase, after a short delay so the local fly has time to settle.
        if (!events || events.length === 0) {
            if (onDone) onDone();
            return;
        }

        const STEP_MS = 1100;   // slightly slower than aivai showcase
        let i = 0;

        const renderOne = () => {
            const ev = events[i];
            const opts = { animate: true };
            if (skipFirstRaw && i === 0) opts.skipRaw = true;
            this._renderEvent(ev, opts);
            i += 1;
            if (i < events.length) {
                this.animTimers.push(setTimeout(renderOne, STEP_MS));
            } else {
                this.animTimers.push(setTimeout(() => {
                    if (onDone) onDone();
                }, 300));
            }
        };

        // If we already rendered the local fly, give it ~400ms to land
        // before the mechanic-phase banner / AI response start playing.
        // Otherwise start immediately.
        if (skipFirstRaw) {
            this.animTimers.push(setTimeout(renderOne, 400));
        } else {
            renderOne();
        }
    },

    _renderEvent(ev, options = {}) {
        // options.animate : default true, set false for instant snap (history view)
        // options.skipRaw : default false, set true to skip the card-fly + raw
        //                   state render (used after we already rendered them
        //                   locally on click for optimistic feedback)
        const animate = options.animate !== false;
        const skipRaw = options.skipRaw === true;

        const before   = ev.before_move;
        const afterRaw = ev.after_raw_move;

        if (!skipRaw) {
            this._setActiveTurn(ev.player);

            // Capture the source card's position BEFORE we re-render the hand,
            // so we can fly it to the "Just played" slot (FLIP technique).
            let sourceRect = null;
            if (animate && typeof ev.card_index === 'number' && ev.card_index >= 0
                        && ev.card_played != null) {
                sourceRect = this._captureCardRect(ev.player, ev.card_index);
            }

            this._renderHands(afterRaw, /*clickable=*/false);
            this._renderScores(afterRaw, before);
            this._renderPlayed(ev.player, ev.card_played, true);
            this._renderPlayed(ev.player === 1 ? 2 : 1, null, false);
            this.turnNum.textContent = (ev.turn || 0) + 1;

            if (sourceRect) {
                this._flyCardFromSourceTo(ev.player, sourceRect);
            }
        }

        const fired = (ev.mechanics || []).filter(m => m.fired);
        if (fired.length === 0) {
            this._clearBanner();
            this._clearMechanicAffected();
        } else {
            this._showMechanicBanner(fired);
            const finalState = ev.after || ev.mechanics[ev.mechanics.length - 1].after;
            this._renderScores(finalState, before);
            this._renderHands(finalState, /*clickable=*/false);
            this._highlightAffectedPlayers(fired);
        }
    },

    // Optimistic local render of the human's raw move (no mechanics).
    // Builds a synthetic "after_raw" state by popping the played card and
    // adding its value to the score, then runs _renderEvent's raw phase so
    // the user sees the card fly to the played slot instantly on click.
    // Returns true if a render actually happened (so the caller knows to
    // pass skipRaw=true when the server response arrives).
    _renderLocalRawMove(beforeState, cardIndex) {
        if (!beforeState || cardIndex == null || cardIndex < 0) return false;
        const hand1Source = beforeState.hands[1] || beforeState.hands['1'] || [];
        if (cardIndex >= hand1Source.length) return false;

        const card = hand1Source[cardIndex];
        const hand1 = hand1Source.slice();
        hand1.splice(cardIndex, 1);
        const hand2 = (beforeState.hands[2] || beforeState.hands['2'] || []).slice();
        const sc1 = (beforeState.scores[1] != null
                        ? beforeState.scores[1] : beforeState.scores['1']) || 0;
        const sc2 = (beforeState.scores[2] != null
                        ? beforeState.scores[2] : beforeState.scores['2']) || 0;

        const afterRaw = {
            ...beforeState,
            hands:       { 1: hand1, 2: hand2 },
            scores:      { 1: sc1 + card, 2: sc2 },
            last_played: card,
        };

        const fakeEvent = {
            turn:           beforeState.turn || 0,
            player:         1,
            card_index:     cardIndex,
            card_played:    card,
            before_move:    beforeState,
            after_raw_move: afterRaw,
            mechanics:      [],   // empty → mechanic phase just clears banner
            after:          afterRaw,
        };
        this._renderEvent(fakeEvent, { animate: true });
        return true;
    },

    // FLIP step 1: read the position of the card that's about to leave the hand.
    _captureCardRect(player, cardIndex) {
        const handEl = document.getElementById(`playme-hand-${player}`);
        if (!handEl) return null;
        const cards = handEl.querySelectorAll('.aivai-card');
        if (cardIndex < 0 || cardIndex >= cards.length) return null;
        return cards[cardIndex].getBoundingClientRect();
    },

    // FLIP step 2-4: invert the just-played card to the source location, then
    // transition back to identity so it visually flies into the played slot.
    _flyCardFromSourceTo(player, sourceRect) {
        const slot = document.getElementById(`playme-played-${player}`);
        if (!slot) return;
        const cardEl = slot.querySelector('.aivai-card');
        if (!cardEl) return;

        const destRect = cardEl.getBoundingClientRect();
        const dx = sourceRect.left - destRect.left;
        const dy = sourceRect.top  - destRect.top;
        // Hand cards are smaller than the just-played slot card; scale up
        // from source size for a "card grows as it lands" effect.
        const scaleStart = destRect.width
            ? Math.max(0.2, sourceRect.width / destRect.width)
            : 0.85;

        // Apply the inverse transform with no transition (visually at source).
        cardEl.style.transition       = 'none';
        cardEl.style.transformOrigin  = 'top left';
        cardEl.style.transform        = `translate(${dx}px, ${dy}px) scale(${scaleStart})`;
        cardEl.style.opacity          = '0.92';
        cardEl.style.zIndex           = '5';

        // Flush layout so the inverse state is committed before we transition.
        void cardEl.offsetWidth;

        cardEl.style.transition =
            'transform 0.34s cubic-bezier(0.2, 0.75, 0.3, 1), opacity 0.28s ease';
        cardEl.style.transform = 'translate(0, 0) scale(1)';
        cardEl.style.opacity   = '1';

        // Clean up inline styles after the transition so subsequent renders
        // don't inherit them. 380ms gives a small buffer past the 340ms transition.
        const cleanupTimer = setTimeout(() => {
            cardEl.style.transition      = '';
            cardEl.style.transform       = '';
            cardEl.style.transformOrigin = '';
            cardEl.style.opacity         = '';
            cardEl.style.zIndex          = '';
        }, 380);
        this.animTimers.push(cleanupTimer);
    },

    _renderState(state, prev, clickable) {
        if (!state) return;
        this._setActiveTurn(state.current_player);
        this._renderHands(state, clickable);
        this._renderScores(state, prev);
        this._renderPlayed(1, null, false);
        this._renderPlayed(2, null, false);
        this.turnNum.textContent = state.turn || 0;
    },

    _renderHands(state, p1Clickable) {
        for (const p of [1, 2]) {
            const handEl = document.getElementById(`playme-hand-${p}`);
            if (!handEl) continue;
            const hand = (state.hands || {})[p] || (state.hands || {})[String(p)] || [];
            handEl.innerHTML = '';
            hand.forEach((val, idx) => {
                const card = document.createElement('span');
                card.className = 'aivai-card';
                card.textContent = val;
                if (p === 1 && p1Clickable) {
                    card.classList.add('playme-card-clickable');
                    card.addEventListener('click', () => this.onCardClick(idx));

                    // Pulse highlight on P1 cards that would trigger a
                    // mechanic if played. Mirrors the phone view's behavior.
                    const triggerNames = (this.handTriggers || [])[idx] || [];
                    const mechSlots = triggerNames
                        .map(n => (this.loadout || []).findIndex(m => m && m.name === n) + 1)
                        .filter(slot => slot >= 1 && slot <= 2);
                    const uniqueSlots = [...new Set(mechSlots)];
                    if (uniqueSlots.length === 1) {
                        card.classList.add(`pulse-mech-${uniqueSlots[0]}`);
                    } else if (uniqueSlots.length >= 2) {
                        card.classList.add('pulse-both');
                    }
                }
                handEl.appendChild(card);
            });
        }
    },

    _setHandClickable(clickable) {
        if (!this.state) return;
        this._renderHands(this.state, clickable && this.state.current_player === 1 && !this.finished);
    },

    _renderScores(state, prev) {
        for (const p of [1, 2]) {
            const scoreEl = document.getElementById(`playme-score-${p}`);
            if (!scoreEl) continue;
            const newVal = (state.scores || {})[p] ?? (state.scores || {})[String(p)] ?? 0;
            const oldVal = prev
                ? ((prev.scores || {})[p] ?? (prev.scores || {})[String(p)] ?? 0)
                : newVal;
            scoreEl.textContent = newVal;
            scoreEl.classList.remove('flash-up', 'flash-down', 'flash-reset');
            if (newVal > oldVal) {
                scoreEl.classList.add('flash-up');
            } else if (newVal === 0 && oldVal > 0) {
                scoreEl.classList.add('flash-reset');
            } else if (newVal < oldVal) {
                scoreEl.classList.add('flash-down');
            }
        }
    },

    _renderPlayed(player, cardValue, justPlayed) {
        const slot = document.getElementById(`playme-played-${player}`);
        if (!slot) return;
        if (cardValue == null) {
            slot.innerHTML = '<span class="dim">&mdash;</span>';
            return;
        }
        slot.innerHTML = '';
        const card = document.createElement('span');
        card.className = 'aivai-card' + (justPlayed ? ' just-played' : '');
        card.textContent = cardValue;
        slot.appendChild(card);
    },

    _setActiveTurn(player) {
        document.getElementById('playme-player-1').classList.toggle('active-turn', player === 1);
        document.getElementById('playme-player-2').classList.toggle('active-turn', player === 2);
    },

    _showMechanicBanner(firedList) {
        this.bannerEl.className = 'aivai-mech-banner fired';
        if (firedList.length === 1) {
            const cls = this.mechColorClass[firedList[0].name] || 'mech-1';
            this.bannerEl.classList.add(cls);
        } else {
            // 'mixed' = neutral banner so each per-name color rule wins,
            // letting the user clearly see both mechanics fired.
            this.bannerEl.classList.add('mixed');
        }
        const lines = firedList.map(f => this._describeFire(f));
        this.bannerEl.innerHTML = lines.map((html, i) =>
            (i > 0 ? '<div class="aivai-mech-banner-effect both-fired">' : '<div>') +
            html + '</div>'
        ).join('');
    },

    _describeFire(firedEvent) {
        const cls = this.mechColorClass[firedEvent.name] || 'mech-1';
        const parts = [];
        const sc = firedEvent.score_changes || {};
        for (const p of ['1', '2']) {
            if (sc[p]) {
                const delta = sc[p].after - sc[p].before;
                if (sc[p].after === 0 && sc[p].before > 0) {
                    parts.push(`P${p} score reset to 0 (was ${sc[p].before})`);
                } else if (delta > 0) {
                    parts.push(`P${p} +${delta} -> ${sc[p].after}`);
                } else if (delta < 0) {
                    parts.push(`P${p} ${delta} -> ${sc[p].after}`);
                }
            }
        }
        const hc = firedEvent.hand_changes || {};
        for (const p of ['1', '2']) {
            if (hc[p]) parts.push(`P${p} hand changed`);
        }
        if (firedEvent.extra_turn_changed) parts.push('Extra turn granted');
        const effect = parts.join('. ') || 'Effect applied';
        // Apply both `${cls}-name` (legacy AI vs AI selector) AND `${cls}`
        // (new per-name selector) so the multi-fire case picks up the color.
        return (
            `<div class="aivai-mech-banner-name ${cls}-name ${cls}">${firedEvent.name}</div>` +
            `<div class="aivai-mech-banner-effect">${effect}</div>`
        );
    },

    _highlightAffectedPlayers(firedList) {
        this._clearMechanicAffected();
        for (const f of firedList) {
            const cls = this.mechColorClass[f.name] || 'mech-1';
            const sc = f.score_changes || {};
            const hc = f.hand_changes  || {};
            for (const p of ['1', '2']) {
                if (sc[p] || hc[p]) {
                    document.getElementById(`playme-player-${p}`)
                        .classList.add('mechanic-affected', cls);
                }
            }
        }
    },

    _clearMechanicAffected() {
        for (const p of [1, 2]) {
            const el = document.getElementById(`playme-player-${p}`);
            if (el) el.classList.remove('mechanic-affected', 'mech-1', 'mech-2');
        }
    },

    _clearBanner() {
        this.bannerEl.className = 'aivai-mech-banner';
        this.bannerEl.innerHTML = '<span class="dim">No mechanic fired yet</span>';
    },

    _showResult() {
        if (!this.state) return;
        const w  = this._winner();
        const s1 = (this.state.scores || {})[1] ?? this.state.scores['1'] ?? 0;
        const s2 = (this.state.scores || {})[2] ?? this.state.scores['2'] ?? 0;

        let title;
        if (w === 1) {
            title = 'You win!';
            this.resultEl.className = 'aivai-result winner-1';
        } else if (w === 2) {
            title = 'AI wins';
            this.resultEl.className = 'aivai-result winner-2';
        } else {
            if (s1 > s2) {
                title = 'You lead (no winner reached 45)';
                this.resultEl.className = 'aivai-result winner-1';
            } else if (s2 > s1) {
                title = 'AI leads (no winner reached 45)';
                this.resultEl.className = 'aivai-result winner-2';
            } else {
                title = 'Draw';
                this.resultEl.className = 'aivai-result draw';
            }
        }
        this.resultEl.innerHTML =
            `<div class="aivai-result-title">${title}</div>` +
            `<div class="aivai-result-detail">Final scores  P1 ${s1}  &middot;  P2 ${s2}</div>`;
        this.resultEl.classList.remove('hidden');

        // Scroll the result into view so the user sees the win/loss banner
        // even if the playme section was partially below the fold. A short
        // delay lets the unhide commit before scrolling.
        this.animTimers.push(setTimeout(() => {
            this.resultEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 50));
    },

    _winner() {
        if (!this.state || !this.state.scores) return null;
        const s1 = this.state.scores[1] ?? this.state.scores['1'] ?? 0;
        const s2 = this.state.scores[2] ?? this.state.scores['2'] ?? 0;
        if (s1 >= 45 && s1 > s2) return 1;
        if (s2 >= 45 && s2 > s1) return 2;
        return null;
    },

    _cancelAnimations() {
        this.animTimers.forEach(t => clearTimeout(t));
        this.animTimers = [];
    },

    // ── History scrubbing ──────────────────────────────────────────────────
    //
    // _viewIdx semantics:
    //   null               : no game yet
    //   0                  : viewing the initial state (before any moves)
    //   k in [1, len]      : viewing state immediately after event k-1
    //   len === history.length : "live" — interactive, hand re-enabled

    stepBack() {
        if (this.busy || this._viewIdx === null) return;
        if (this._viewIdx <= 0) return;
        // Cancel any in-flight auto-pass so it does not fire while the user
        // is reviewing history.
        this._cancelAnimations();
        this._viewIdx -= 1;
        this._renderAtViewIdx();
    },

    stepForward() {
        if (this.busy || this._viewIdx === null) return;
        if (this._viewIdx >= this._history.length) return;
        this._viewIdx += 1;
        this._renderAtViewIdx();
    },

    _renderAtViewIdx() {
        const idx = this._viewIdx;
        const isLive = (idx === this._history.length);

        if (idx === 0) {
            this._renderInitialFromHistory();
        } else {
            this._renderEvent(this._history[idx - 1], /*animate=*/false);
        }

        this.turnNum.textContent   = idx;
        this.turnTotal.textContent = this._history.length;

        if (isLive) {
            // Returning to live state: re-enable interactivity and update status.
            if (this.finished) {
                this._showResult();
                this.statusEl.textContent = 'Game over';
            } else if (this.state && this.state.current_player === 1) {
                this.statusEl.textContent = 'Your turn';
                this._setHandClickable(true);
                this._maybeAutoPass();
            } else {
                this.statusEl.textContent = 'AI thinking...';
            }
        } else {
            this.resultEl.classList.add('hidden');
            // Don't call _setHandClickable here — it would re-render the hand
            // using this.state (the LIVE state), wiping out the historical
            // hand that _renderEvent / _renderInitialFromHistory just drew.
            // _renderEvent already renders the hand non-clickable in history
            // mode, which is what we want.
            this.statusEl.textContent =
                `Reviewing turn ${idx} of ${this._history.length} `
                + '(forward to resume)';
        }

        this._updateNavButtons();
    },

    _renderInitialFromHistory() {
        if (!this._initialState) return;
        const s = this._initialState;
        this._setActiveTurn(s.current_player);
        this._renderHands(s, /*clickable=*/false);
        this._renderScores(s, null);
        this._renderPlayed(1, null, false);
        this._renderPlayed(2, null, false);
        this._clearBanner();
        this._clearMechanicAffected();
    },

    _updateNavButtons() {
        if (!this.backBtn || !this.forwardBtn) return;
        const idx = this._viewIdx;
        if (idx === null || this.busy) {
            this.backBtn.disabled    = true;
            this.forwardBtn.disabled = true;
            return;
        }
        this.backBtn.disabled    = (idx <= 0);
        this.forwardBtn.disabled = (idx >= this._history.length);
    },
};


// ── Pair Lab tab ─────────────────────────────────────────────────────────────
//
// Streams a pair-vs-singleton evaluation from the backend over SSE,
// shows live progress, then renders a sortable table when done.

const PAIRLAB_STORAGE_KEY = 'dv-pairlab-results';

const pairlabManager = {
    eventSource:  null,
    results:      [],     // accumulated combo_done results
    totalCombos:  0,
    startTime:    0,
    running:      false,
    sortKey:      'composite',
    sortDir:      -1,    // -1 = descending (highest first), +1 = ascending
    showSingles:  true,
    showPairs:    true,
    _startMeta:   null,   // run config stashed when 'start' SSE event fires

    init() {
        this.runBtn        = document.getElementById('pairlab-run-btn');
        this.stopBtn       = document.getElementById('pairlab-stop-btn');
        this.clearBtn      = document.getElementById('pairlab-clear-btn');
        this.gamesInput    = document.getElementById('pairlab-games');
        this.simsInput     = document.getElementById('pairlab-sims');
        this.simsLabel     = document.getElementById('pairlab-sims-label');
        this.depthInput    = document.getElementById('pairlab-depth');
        this.depthLabel    = document.getElementById('pairlab-depth-label');
        this.agentEl       = document.getElementById('pairlab-agent');
        this.statusEl      = document.getElementById('pairlab-status');
        this.progressEl    = document.getElementById('pairlab-progress');
        this.progressFill  = document.getElementById('pairlab-progress-fill');
        this.progressText  = document.getElementById('pairlab-progress-text');
        this.progressTime  = document.getElementById('pairlab-progress-elapsed');
        this.resultsEl     = document.getElementById('pairlab-results');
        this.resultsCount  = document.getElementById('pairlab-results-count');
        this.tbodyEl       = document.getElementById('pairlab-tbody');
        this.theadRowEl    = document.getElementById('pairlab-thead-row');
        this.singlesEl     = document.getElementById('pairlab-show-singles');
        this.pairsEl       = document.getElementById('pairlab-show-pairs');

        this.runBtn.addEventListener('click',   () => this.start());
        this.stopBtn.addEventListener('click',  () => this.stop());
        this.clearBtn.addEventListener('click', () => this.clear());
        this.agentEl.addEventListener('change', () => this._onAgentTypeChange());
        this.singlesEl.addEventListener('change', () => {
            this.showSingles = this.singlesEl.checked;
            this.renderTable();
        });
        this.pairsEl.addEventListener('change', () => {
            this.showPairs = this.pairsEl.checked;
            this.renderTable();
        });

        // Click a sortable header to sort by that column. Clicking the
        // already-active column flips the sort direction; clicking a new
        // column resets to descending (since most metrics are "higher is
        // better" and the user usually wants the top values up top).
        this.theadRowEl.querySelectorAll('th.sortable').forEach(th => {
            th.addEventListener('click', () => {
                const newKey = th.dataset.sortKey;
                if (this.sortKey === newKey) {
                    this.sortDir = -this.sortDir;
                } else {
                    this.sortKey = newKey;
                    this.sortDir = -1;
                }
                this._updateSortIndicator();
                this.renderTable();
            });
        });
        this._updateSortIndicator();

        // Restore any previously-saved results so the user doesn't lose
        // them on a page refresh.
        this._loadSaved();

        // Clicking a row launches that combo in the AI vs AI tab. We bind on
        // tbody and read the row's data-names attr so the listener works for
        // rows added later.
        this.tbodyEl.addEventListener('click', (e) => {
            const tr = e.target.closest('tr[data-names]');
            if (!tr) return;
            let names;
            try { names = JSON.parse(tr.dataset.names); } catch { return; }
            if (Array.isArray(names) && names.length > 0) {
                this._launchInAivai(names);
            }
        });
    },

    _updateSortIndicator() {
        this.theadRowEl.querySelectorAll('th.sortable').forEach(th => {
            const isActive = (th.dataset.sortKey === this.sortKey);
            th.classList.toggle('sort-active', isActive);
            if (isActive) {
                th.dataset.sortDir = (this.sortDir === 1) ? 'asc' : 'desc';
            } else {
                delete th.dataset.sortDir;
            }
        });
    },

    clear() {
        this.results = [];
        this._startMeta = null;
        this.tbodyEl.innerHTML = '';
        this.resultsEl.classList.add('hidden');
        this.progressEl.classList.add('hidden');
        this.statusEl.textContent = 'Cleared';
        try { localStorage.removeItem(PAIRLAB_STORAGE_KEY); } catch (e) { /* ignore */ }
    },

    _launchInAivai(names) {
        switchTab('aivai');
        // startNewGameWithLoadout will fire the match request with these names
        aivaiManager.startNewGameWithLoadout(names);
    },

    _onAgentTypeChange() {
        const isMinimax = (this.agentEl.value === 'minimax');
        this.simsLabel.classList.toggle('hidden', isMinimax);
        this.depthLabel.classList.toggle('hidden', !isMinimax);
    },

    // ── Persistence ────────────────────────────────────────────────────────

    _saveResults() {
        try {
            const payload = {
                results:   this.results,
                meta:      this._startMeta || null,
                timestamp: Date.now(),
            };
            localStorage.setItem(PAIRLAB_STORAGE_KEY, JSON.stringify(payload));
        } catch (e) { /* quota or private mode — silent fail is fine */ }
    },

    _loadSaved() {
        let payload = null;
        try {
            const raw = localStorage.getItem(PAIRLAB_STORAGE_KEY);
            if (!raw) return;
            payload = JSON.parse(raw);
        } catch (e) { return; }

        if (!payload || !Array.isArray(payload.results) || payload.results.length === 0) {
            return;
        }

        this.results    = payload.results;
        this._startMeta = payload.meta || null;
        this.totalCombos = (payload.meta && payload.meta.total_combos) || this.results.length;

        this.renderTable();
        this.resultsEl.classList.remove('hidden');

        // Build a clear status line so the user knows these are restored,
        // not freshly computed.
        const ts = payload.timestamp ? new Date(payload.timestamp) : null;
        const meta = payload.meta || {};
        const aiTag = (meta.agent_type === 'minimax')
            ? `minimax depth ${meta.depth}`
            : (meta.agent_type ? `MCTS ${meta.simulations} sims` : '');
        const stamp = ts ? ts.toLocaleString() : 'previous run';
        this.statusEl.textContent =
            `Showing ${this.results.length} saved result${this.results.length === 1 ? '' : 's'} from ${stamp}`
            + (aiTag ? ` (${aiTag})` : '')
            + ' — click Clear Results to wipe.';
    },

    start() {
        if (this.running) return;
        this.results     = [];
        this.totalCombos = 0;
        this.startTime   = Date.now();
        this.running     = true;
        this.tbodyEl.innerHTML = '';
        this.resultsEl.classList.add('hidden');
        this.progressEl.classList.remove('hidden');
        this.progressFill.style.width = '0%';
        this.progressText.textContent = 'Starting...';
        this.progressTime.textContent = '';
        this.runBtn.classList.add('hidden');
        this.stopBtn.classList.remove('hidden');
        this.statusEl.textContent = 'Running';

        const games = parseInt(this.gamesInput.value, 10) || 30;
        const sims  = parseInt(this.simsInput.value, 10) || 50;
        const depth = parseInt(this.depthInput.value, 10) || 4;
        const agent = this.agentEl.value || 'mcts';
        const url =
            `/api/pair-eval/stream?top_n=10&games=${games}` +
            `&sims=${sims}&depth=${depth}&agent_type=${encodeURIComponent(agent)}`;

        this.eventSource = new EventSource(url);

        this.eventSource.addEventListener('start', (e) => {
            const d = JSON.parse(e.data);
            this.totalCombos = d.total_combos;
            this._startMeta  = d;   // stashed for localStorage persistence
            const aiTag = (d.agent_type === 'minimax')
                ? `minimax depth ${d.depth}`
                : `MCTS ${d.simulations} sims`;
            this.progressText.textContent =
                `Running ${d.total_combos} combos (${d.n_singletons} singles + ${d.n_pairs} pairs) at ${aiTag}`;
        });

        this.eventSource.addEventListener('combo_start', (e) => {
            const d = JSON.parse(e.data);
            this.progressText.textContent =
                `Combo ${d.index + 1} / ${d.total_combos}: ${d.kind} ${d.names.join(' + ')}`;
        });

        this.eventSource.addEventListener('combo_progress', (e) => {
            const d = JSON.parse(e.data);
            const overallDone = d.index + (d.games_done / d.games_total);
            const pct = (overallDone / this.totalCombos) * 100;
            this.progressFill.style.width = `${pct.toFixed(1)}%`;
            this._tickElapsed();
        });

        this.eventSource.addEventListener('combo_done', (e) => {
            const d = JSON.parse(e.data);
            this.results.push(d.result);
            // Render incrementally so the user sees results filling in
            this.renderTable();
            this.resultsEl.classList.remove('hidden');
            const overallDone = d.index + 1;
            const pct = (overallDone / this.totalCombos) * 100;
            this.progressFill.style.width = `${pct.toFixed(1)}%`;
            this._tickElapsed();
            // Persist after each combo so a stopped/refreshed run still
            // keeps the partial results.
            this._saveResults();
        });

        this.eventSource.addEventListener('all_done', (e) => {
            const d = JSON.parse(e.data);
            this.results = d.results;
            this.renderTable();
            this.progressFill.style.width = '100%';
            this.progressText.textContent = 'Complete';
            this.statusEl.textContent = `Done in ${this._elapsedStr()}`;
            this._saveResults();
            this._cleanup();
        });

        this.eventSource.addEventListener('error', (e) => {
            // Either a backend "error" event with data, or a connection error
            try {
                const d = JSON.parse(e.data);
                this.statusEl.textContent = `Error: ${d.message}`;
            } catch (_) {
                this.statusEl.textContent = 'Connection error';
            }
            this._cleanup();
        });
    },

    stop() {
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
        this.statusEl.textContent = 'Stopped';
        this._cleanup();
    },

    _cleanup() {
        this.running = false;
        this.runBtn.classList.remove('hidden');
        this.stopBtn.classList.add('hidden');
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
    },

    _tickElapsed() {
        this.progressTime.textContent = `Elapsed ${this._elapsedStr()}`;
    },

    _elapsedStr() {
        const sec = Math.floor((Date.now() - this.startTime) / 1000);
        const m = Math.floor(sec / 60);
        const s = sec % 60;
        return m > 0 ? `${m}m ${s}s` : `${s}s`;
    },

    renderTable() {
        const filtered = this.results.filter(r =>
            (r.kind === 'single' && this.showSingles) ||
            (r.kind === 'pair'   && this.showPairs)
        );
        const getVal = (entry) => {
            if (this.sortKey === 'composite')   return entry.composite || 0;
            if (this.sortKey === 'avg_length')  return (entry.summary && entry.summary.avg_length) || 0;
            return (entry.components && entry.components[this.sortKey]) || 0;
        };
        const sorted = filtered.slice().sort((a, b) => {
            // sortDir = -1 (descending): want larger values first → b - a
            // sortDir = +1 (ascending) : want smaller values first → a - b
            return (getVal(a) - getVal(b)) * this.sortDir;
        });

        this.resultsCount.textContent = `(${sorted.length} of ${this.results.length})`;
        this.tbodyEl.innerHTML = '';
        sorted.forEach((r, idx) => {
            const tr = document.createElement('tr');
            tr.className = `kind-${r.kind}` + (idx < 3 ? ' top-rank' : '');
            tr.dataset.names = JSON.stringify(r.names);
            tr.title = `Click to launch ${r.names.join(' + ')} in the AI vs AI tab`;

            const c = r.components || {};

            tr.innerHTML = `
                <td class="numeric rank-cell">${idx + 1}</td>
                <td class="kind-cell">${r.kind}</td>
                <td><div class="mech-list">${r.names.map(n => `<span class="mech-pill">${n}</span>`).join('')}</div></td>
                <td class="composite">${r.composite.toFixed(3)}</td>
                <td class="${this._cls(c.balance)}">${this._fmt(c.balance)}</td>
                <td class="${this._cls(c.decisiveness)}">${this._fmt(c.decisiveness)}</td>
                <td class="${this._cls(c.both_meaningful)}">${this._fmt(c.both_meaningful)}</td>
                <td class="${this._cls(c.length_sanity)}">${this._fmt(c.length_sanity)}</td>
                <td class="numeric">${(r.summary && r.summary.avg_length) || ''}</td>
            `;
            this.tbodyEl.appendChild(tr);
        });
    },

    _fmt(v) {
        if (v === undefined || v === null) return '—';
        return v.toFixed(2);
    },

    _cls(v) {
        if (v === undefined || v === null) return 'pairlab-component-cell';
        const base = 'pairlab-component-cell ';
        if (v >= 0.75) return base + 'high';
        if (v >= 0.40) return base + 'mid';
        return base + 'low';
    },
};


// ── Phone Mode modal ────────────────────────────────────────────────────────
//
// Opens a modal with a QR code pointing at /phone on this laptop's LAN IP.
// Phone scans, opens the page, plays vs AI on the phone. Requires uvicorn
// to be bound to 0.0.0.0 (not just 127.0.0.1) so the phone can reach it.

const phoneModal       = document.getElementById('phone-modal');
const phoneUrlEl       = document.getElementById('phone-url');
const phoneQrEl        = document.getElementById('phone-qr');
const phoneOpenBtn     = document.getElementById('playme-phone-btn');
const phoneCloseBtn    = document.getElementById('phone-modal-close');

if (phoneOpenBtn) {
    phoneOpenBtn.addEventListener('click', async () => {
        phoneUrlEl.textContent = 'Loading...';
        phoneQrEl.innerHTML = '';
        phoneModal.classList.remove('hidden');
        try {
            const resp = await fetch('/api/phone/info');
            const data = await resp.json();
            let url  = data.url || `http://${data.host}:${data.port}/phone`;

            // Build the phone URL with the dashboard's current settings:
            //   - mechs : the loadout names shown in the chip strip
            //   - ai    : 'mcts' or 'minimax' from the Play vs AI dropdown
            //   - sims  : MCTS sims (when ai=mcts), from the Sims input
            //   - depth : minimax depth (when ai=minimax), from the Depth input
            // This way the phone session mirrors whatever the dashboard
            // operator picked, without exposing extra controls on the phone.
            const qsParts = [];

            const loadoutNames = (typeof aivaiManager !== 'undefined' && aivaiManager.loadout)
                ? aivaiManager.loadout.map(m => m.name).filter(Boolean)
                : [];
            if (loadoutNames.length > 0) {
                qsParts.push(`mechs=${encodeURIComponent(loadoutNames.join(','))}`);
            }

            const aiSelect = document.getElementById('playme-agent');
            const simsInp  = document.getElementById('playme-sims');
            const depthInp = document.getElementById('playme-depth');
            const aiType   = (aiSelect && aiSelect.value) || 'mcts';
            qsParts.push(`ai=${encodeURIComponent(aiType)}`);
            if (aiType === 'mcts') {
                const sims = parseInt(simsInp && simsInp.value, 10) || 200;
                qsParts.push(`sims=${sims}`);
            } else {
                const depth = parseInt(depthInp && depthInp.value, 10) || 8;
                qsParts.push(`depth=${depth}`);
            }

            if (qsParts.length > 0) {
                const sep = url.includes('?') ? '&' : '?';
                url = `${url}${sep}${qsParts.join('&')}`;
            }

            phoneUrlEl.textContent = url;
            // Use the public api.qrserver.com QR generator. It returns a
            // PNG for the given data string. No tracking, no JS dependency.
            const qrSrc = 'https://api.qrserver.com/v1/create-qr-code/'
                        + '?size=240x240&margin=4&data='
                        + encodeURIComponent(url);
            const img = document.createElement('img');
            img.src = qrSrc;
            img.alt = 'QR code';
            phoneQrEl.appendChild(img);

            // Adjust hint text based on which connection method we're using.
            const subEl  = document.querySelector('.phone-modal-sub');
            const hintEl = document.querySelector('.phone-modal-hint');
            if (data.source === 'ngrok') {
                if (subEl)  subEl.innerHTML  = 'Connected via <strong>ngrok</strong>. Works on any network, including cellular.';
                if (hintEl) hintEl.textContent = 'You may see an ngrok splash page on first visit — tap "Visit Site" to continue.';
            } else {
                if (subEl)  subEl.innerHTML  = 'Make sure your phone is on the same WiFi network as this laptop, and that you started the server with <code>--host 0.0.0.0</code>.';
                if (hintEl) hintEl.textContent = "If the page doesn't load, your network is probably blocking device-to-device traffic. Run ngrok to bypass it.";
            }
        } catch (e) {
            phoneUrlEl.textContent = `Error: ${e}`;
        }
    });
}

if (phoneCloseBtn) {
    phoneCloseBtn.addEventListener('click', () => phoneModal.classList.add('hidden'));
}

if (phoneModal) {
    phoneModal.addEventListener('click', (e) => {
        if (e.target === phoneModal) phoneModal.classList.add('hidden');
    });
}


// ── Startup ──────────────────────────────────────────────────────────────────

libraryManager.init();
aivaiManager.init();
playMeManager.init();
pairlabManager.init();

// Restore the last-active tab so a hard refresh doesn't kick the user back
// to the Pipeline view. Falls back silently if localStorage is unavailable.
try {
    const savedTab = localStorage.getItem('dv-active-tab');
    if (savedTab && ['pipeline', 'library', 'aivai', 'pairlab'].includes(savedTab)) {
        switchTab(savedTab);
    }
} catch (e) { /* private mode or storage disabled — keep default tab */ }
