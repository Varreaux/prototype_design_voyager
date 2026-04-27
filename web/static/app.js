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
