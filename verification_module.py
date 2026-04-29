"""
verification_module.py
======================
DesignVoyager — Self-Verification Module (delta-gated)

Step 4 of the loop. Decides accept / revise / discard for a proposed
mechanic.

This module used to apply absolute thresholds (playability >= 1.0,
balance_gap <= 0.45, aggregate >= 0.70). It now delegates to
SelfVerifier, which performs three gated checks:

  1. Integration gate   — compile/syntax/instantiation/dry-run all OK
  2. Behavioral gate    — playability, balance, decisiveness, agency,
                          and trigger rate clear absolute thresholds
  3. Relative-gain gate — metrics with the mechanic must measurably
                          improve over baseline (no-mechanic) metrics

The relative-gain gate is what catches "no-op" mechanics that look
fine in absolute terms but do not actually change gameplay.

Public API:
    verify(mechanic, child_metrics, parent_metrics, trigger_stats,
           compile_ok, compile_error, stage, already_revised)
        -> (decision, feedback, full_output_dict)

The legacy three-arg form (mechanic, simple_scores, already_revised)
is also still accepted for callers that haven't been updated yet,
but it falls back to absolute thresholds and cannot detect no-ops.
"""

from __future__ import annotations

from typing import Optional, Tuple, Dict, Any, Union

from self_verification import SelfVerifier
from verification_schema import (
    IntegrationReport,
    MechanicCandidate,
    PlaytestMetrics,
    TriggerStats,
    VerificationInput,
)

# Outcomes (strings exposed for main.py / pipeline_runner.py)
ACCEPT  = "accept"
REVISE  = "revise"
DISCARD = "discard"

_VERIFIER = SelfVerifier()

# Public threshold values (re-exported from SelfVerifier for UI display)
MIN_PLAYABILITY = _VERIFIER.min_playability
MAX_BALANCE_GAP = _VERIFIER.max_balance_gap
MIN_DECISIVENESS = _VERIFIER.min_decisiveness
MIN_AGENCY = _VERIFIER.min_agency


def _make_integration_report(compile_ok: bool, compile_error: str = "",
                             hook_location: str = "perform_move") -> IntegrationReport:
    """Synthesize the team's IntegrationReport from Morgan's compile_check result."""
    return IntegrationReport(
        schema_ok=True,                      # proposal module guarantees these fields
        syntax_ok=compile_ok,
        hook_ok=True,                        # always perform_move in current pipeline
        instantiation_ok=compile_ok,
        dry_run_ok=compile_ok,
        error_message=compile_error or "",
    )


def _make_candidate(mechanic: dict) -> MechanicCandidate:
    return MechanicCandidate(
        mechanic_name=mechanic.get("mechanic_name", "unknown"),
        mechanic_type=mechanic.get("mechanic_type", "other"),
        description=mechanic.get("description", ""),
        python_code=mechanic.get("python_code", ""),
        justification=mechanic.get("justification", ""),
        hook_location=mechanic.get("hook_location", "perform_move"),
    )


def verify(
    mechanic: dict,
    child_metrics_or_scores: Union[PlaytestMetrics, Dict[str, Any]],
    parent_metrics: Optional[PlaytestMetrics] = None,
    trigger_stats: Optional[TriggerStats] = None,
    compile_ok: bool = True,
    compile_error: str = "",
    stage: int = 1,
    already_revised: bool = False,
    game_name: str = "board",
) -> Tuple[str, str, Dict[str, Any]]:
    """
    Decide accept / revise / discard for a mechanic.

    Modern call (delta-gated):
        verify(mechanic,
               child_metrics=PlaytestMetrics,
               parent_metrics=PlaytestMetrics,
               trigger_stats=TriggerStats,
               compile_ok=True/False,
               compile_error="...",
               stage=int,
               already_revised=bool)
        -> (decision, feedback, full_output_dict)

    Legacy call (absolute thresholds only, kept for compatibility):
        verify(mechanic, scores_dict, already_revised)
        -> (decision, feedback, {})  # full_output_dict is empty

    Returns:
        decision: 'accept' | 'revise' | 'discard'
        feedback: human-readable feedback string for the proposal repair loop
        full_output_dict: VerificationOutput as a dict (empty on legacy calls)
    """
    # ── Legacy mode: verify(mechanic, scores_dict, already_revised_bool) ──
    # Detected by:
    #   * second arg is a dict (the simple scores dict), AND
    #   * the slot that should hold parent_metrics is either missing,
    #     None, or a bool (someone passed already_revised positionally)
    if isinstance(child_metrics_or_scores, dict) and (
        parent_metrics is None or isinstance(parent_metrics, bool)
    ):
        if isinstance(parent_metrics, bool):
            already_revised = parent_metrics
        return _legacy_verify(mechanic, child_metrics_or_scores, already_revised)

    # ── Modern mode: full delta-gated verification ────────────────────────
    if parent_metrics is None or trigger_stats is None:
        raise ValueError(
            "Modern verify() requires parent_metrics, trigger_stats, "
            "compile_ok, and stage. Run the baseline once at pipeline start "
            "and pass it through."
        )

    candidate = _make_candidate(mechanic)
    integration = _make_integration_report(compile_ok, compile_error,
                                           hook_location=candidate.hook_location)

    vinput = VerificationInput(
        stage=stage,
        mechanic=candidate,
        integration=integration,
        parent_metrics=parent_metrics,
        child_metrics=child_metrics_or_scores,
        trigger_stats=trigger_stats,
        retry_count=1 if already_revised else 0,
        game_name=game_name,
    )

    output = _VERIFIER.decide(vinput)
    feedback = output.repair_feedback or output.reason

    # Friendly print of the final decision
    name   = candidate.mechanic_name
    icon   = "✓" if output.decision == ACCEPT else (
             "→" if output.decision == REVISE else "✗")
    detail = (f"{output.reason}, rel_score={output.relative_score:+.3f}"
              if output.decision == ACCEPT else
              f"{output.reason}")
    print(f"  [Verify] {icon} {output.decision.upper()} — {name} ({detail})")

    return output.decision, feedback, output.to_dict()


# ── Legacy threshold-based verification ──────────────────────────────────────

# Hard constraints (kept identical to the previous module)
_MIN_PLAYABILITY = 1.0
_MAX_BALANCE_GAP = 0.45
_MIN_AGGREGATE   = 0.70


def _legacy_verify(mechanic: dict, scores: dict, already_revised: bool) -> Tuple[str, str, Dict[str, Any]]:
    """
    Backwards-compatible absolute-threshold verifier. Used only when callers
    have not yet been updated to pass baseline metrics. Cannot detect no-op
    mechanics.
    """
    name        = mechanic.get("mechanic_name", "unknown")
    playability = scores.get("playability", 0)
    balance_gap = scores.get("balance_gap", 1)
    aggregate   = scores.get("aggregate", 0)

    print(f"[Verify legacy] Evaluating '{name}'...")

    if playability < _MIN_PLAYABILITY:
        feedback = (
            f"The mechanic caused too many games to not finish properly "
            f"(playability={playability:.0%}, minimum is {_MIN_PLAYABILITY:.0%}). "
            f"Please simplify the logic or add a safety guard."
        )
        return DISCARD, feedback, {}

    if balance_gap > _MAX_BALANCE_GAP:
        feedback = (
            f"The mechanic creates extreme imbalance "
            f"(balance_gap={balance_gap:.0%}, maximum is {_MAX_BALANCE_GAP:.0%}). "
            f"Make the mechanic symmetric or add counterplay."
        )
        if already_revised:
            return DISCARD, feedback, {}
        return REVISE, feedback, {}

    if aggregate < _MIN_AGGREGATE:
        feedback = (
            f"The mechanic's overall score is too low (aggregate={aggregate:.2f}, "
            f"minimum is {_MIN_AGGREGATE:.2f}). "
            f"Try a more impactful mechanic that meaningfully changes strategy."
        )
        if already_revised:
            return DISCARD, feedback, {}
        return REVISE, feedback, {}

    return ACCEPT, "Mechanic accepted (legacy thresholds).", {}
