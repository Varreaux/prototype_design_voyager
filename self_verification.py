"""
self_verification.py
====================
DesignVoyager — Delta-Gated Self-Verification.

Adapted from the team's repo. Decides accept/revise/discard for a
proposed mechanic using three gates:

  1. Integration gate  — the mechanic must compile, instantiate, and
                         dry-run cleanly.
  2. Behavioral gate   — absolute metrics (playability, balance, agency,
                         decisiveness, trigger rate) must clear thresholds.
  3. Relative gain gate — the mechanic must measurably improve the game
                          relative to the baseline (no-mechanic) playtest.
                          This is the gate that catches "no-op" mechanics
                          like block_adjacent_on_placement that look fine
                          in absolute terms but do not change gameplay.

The thresholds below are starting values borrowed from the team's repo.
Tune them after running on a few real mechanics and inspecting deltas.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from verification_metrics import (
    build_absolute_metric_dict,
    compute_delta_metrics,
    compute_overall_score,
    compute_relative_score,
    compute_trigger_rate,
)
from verification_schema import VerificationInput, VerificationOutput

try:
    # Optional import: if google-genai or auth is unavailable in some
    # context (tests, the dashboard's static endpoints, etc.) the
    # alignment gate degrades to "skip" instead of breaking the verifier.
    from description_check import check_description_matches_code as _check_alignment
except Exception:    # pragma: no cover, defensive only
    _check_alignment = None


# Mechanics in Morgan's pipeline always run after the move (in perform_move).
# Kept for compatibility with the team's hook-location concept.
ALLOWED_HOOKS = {
    "validate_move",
    "perform_move",
    "game_finished",
    "get_winner",
    "next_player",
}


class SelfVerifier:
    """
    Delta-Gated Verification for DesignVoyager.

    Goals:
    1. Block obviously broken mechanics from entering the library.
    2. Check whether the mechanic actually contributed something positive
       relative to the baseline (no-mechanic) game.
    3. Return structured metadata for the mechanic library and repair loop.
    """

    def __init__(self) -> None:
        # Cheap absolute behavioral gates.
        # min_playability slightly relaxed from team's 0.80 to 0.85 so a
        # single MCTS timeout in 60 games does not tank the rate.
        self.min_playability = 0.85
        self.max_balance_gap = 0.35
        self.min_decisiveness = 0.20
        self.min_agency = 0.40
        self.require_trigger = True

        # Retry policy: one revision attempt before discard
        self.max_retry_before_discard = 1

    def accept_threshold_for_stage(self, stage: int) -> float:
        """
        Relative-score threshold per curriculum stage. Kept small because
        relative deltas across 60+40 stochastic games are typically modest.

        Stage 1 lowered from 0.03 to 0.015 to compensate for the depth-
        crash bias at MCTS 50/10 budget. With the depth penalty removed
        in compute_relative_score, the practical noise floor is lower
        and good mechanics that previously sat at +0.005 to +0.025 can
        now make it through.
        """
        if stage <= 1:
            return 0.015
        if stage == 2:
            return 0.012
        return 0.008

    def build_feedback(self, failure_modes: List[str]) -> str:
        """Produce a human-readable feedback string for the proposal repair loop."""
        templates = {
            "schema_failure": (
                "The mechanic output is missing required fields. Please return a complete mechanic with "
                "name, type, description, justification, code, and hook location."
            ),
            "syntax_failure": (
                "The mechanic code is not valid Python. Please fix syntax errors and return valid code."
            ),
            "hook_failure": (
                "The mechanic is attached to an invalid hook. Please place it in a valid location "
                "such as validate_move, perform_move, game_finished, or get_winner."
            ),
            "instantiation_failure": (
                "The modified game could not be instantiated. Please fix the integration logic "
                "or any missing fields/state."
            ),
            "dry_run_failure": (
                "The mechanic fails during a short dry run. Please fix state transitions or illegal effects "
                "caused immediately after integration."
            ),
            "low_playability": (
                "The mechanic causes too many invalid or non-terminating matches. Please revise it so games "
                "consistently reach valid terminal states."
            ),
            "extreme_imbalance": (
                "The mechanic creates too much player advantage. Please reduce asymmetry or add counterplay."
            ),
            "low_decisiveness": (
                "The mechanic appears to create too many draws or unresolved outcomes. Please make progress "
                "toward victory more decisive."
            ),
            "low_agency": (
                "The mechanic leaves players with too few meaningful choices. Please add branching decisions "
                "or reduce forced-move behavior."
            ),
            "no_trigger": (
                "The mechanic was not meaningfully triggered during playtesting. Please revise the trigger "
                "condition or integration point so it actually affects gameplay."
            ),
            "no_state_change": (
                "The mechanic triggered but did not materially change the game state. Please ensure it changes "
                "board state, legal moves, resources, or terminal conditions."
            ),
            "negative_relative_gain": (
                "The mechanic passes basic checks but does not improve the game relative to the baseline "
                "(no-mechanic) version. The metrics for the game with the mechanic look essentially the same "
                "as without it. Please revise so the mechanic visibly affects strategic depth, balance, or "
                "decisiveness."
            ),
            "description_mismatch": (
                "The mechanic's python_code does not faithfully implement what the description says. "
                "Common cause: the code adds, subtracts, or modifies a value that the description does "
                "not mention (for example, re-adding the played card to the score even though the "
                "description does not mention any double scoring). Please rewrite the code so it matches "
                "the description exactly, or rewrite the description so it matches what the code does."
            ),
        }

        unique_modes: List[str] = []
        seen = set()
        for mode in failure_modes:
            if mode not in seen:
                seen.add(mode)
                unique_modes.append(mode)

        return " ".join(templates[m] for m in unique_modes if m in templates).strip()

    # ── Gates ───────────────────────────────────────────────────────────────

    def check_integration_gate(self, verification_input: VerificationInput) -> Tuple[bool, List[str]]:
        failures: List[str] = []
        mech = verification_input.mechanic
        integration = verification_input.integration

        if not integration.schema_ok:
            failures.append("schema_failure")
        if not integration.syntax_ok:
            failures.append("syntax_failure")
        if mech.hook_location not in ALLOWED_HOOKS or not integration.hook_ok:
            failures.append("hook_failure")
        if not integration.instantiation_ok:
            failures.append("instantiation_failure")
        if not integration.dry_run_ok:
            failures.append("dry_run_failure")

        return len(failures) == 0, failures

    def check_behavioral_gate(self, verification_input: VerificationInput) -> Tuple[bool, List[str], Dict[str, float]]:
        child_abs = build_absolute_metric_dict(verification_input.child_metrics)
        trigger_rate = compute_trigger_rate(verification_input.trigger_stats, by="match")
        failures: List[str] = []

        if child_abs["playability"] < self.min_playability:
            failures.append("low_playability")

        if child_abs["balance_gap"] > self.max_balance_gap:
            failures.append("extreme_imbalance")

        if child_abs["decisiveness"] < self.min_decisiveness:
            failures.append("low_decisiveness")

        if child_abs["agency"] < self.min_agency:
            failures.append("low_agency")

        if self.require_trigger and trigger_rate <= 0:
            failures.append("no_trigger")

        if (
            verification_input.trigger_stats.trigger_count > 0
            and verification_input.trigger_stats.state_changed_by_mechanic_count <= 0
        ):
            failures.append("no_state_change")

        child_abs["trigger_rate"] = trigger_rate
        return len(failures) == 0, failures, child_abs

    def check_description_alignment_gate(
        self, verification_input: VerificationInput
    ) -> Tuple[bool, List[str], str]:
        """
        4th gate: ask a reviewer LLM whether the mechanic's python_code
        actually implements the natural-language description. Catches the
        LLM-hallucination class of bug we saw in the early library, where
        code does extra work (e.g. re-adds the played card to the score)
        that the description does not mention.

        Fails OPEN: if the LLM call errors, returns True so transient
        infrastructure problems do not silently block good mechanics.

        Returns (passed, failure_modes, mismatch_issue). mismatch_issue is
        the reviewer's explanation appended to the standard feedback so
        the proposal repair loop knows what specifically to fix.
        """
        if _check_alignment is None:
            return True, [], ""    # module not importable, skip silently

        mech = verification_input.mechanic
        try:
            matches, issue = _check_alignment(
                mech.description, mech.python_code,
                game_name=verification_input.game_name,
            )
        except Exception as e:
            print(f"  [Verify] description-alignment gate errored, skipping: {e}")
            return True, [], ""

        if matches:
            return True, [], ""
        return False, ["description_mismatch"], issue

    def evaluate_relative_gain(self, verification_input: VerificationInput) -> Tuple[float, Dict[str, float], List[str]]:
        delta = compute_delta_metrics(
            verification_input.parent_metrics,
            verification_input.child_metrics,
        )
        relative_score = compute_relative_score(
            verification_input.parent_metrics,
            verification_input.child_metrics,
            novelty_score=0.0,
        )

        failures: List[str] = []
        if relative_score < self.accept_threshold_for_stage(verification_input.stage):
            failures.append("negative_relative_gain")

        return relative_score, {
            "delta_playability": delta.delta_playability,
            "delta_balance_gap": delta.delta_balance_gap,
            "delta_depth": delta.delta_depth,
            "delta_decisiveness": delta.delta_decisiveness,
            "delta_agency": delta.delta_agency,
            "delta_coverage": delta.delta_coverage,
        }, failures

    # ── Top-level decision ──────────────────────────────────────────────────

    def decide(self, verification_input: VerificationInput) -> VerificationOutput:
        # Stage 1: integration
        integration_pass, integration_failures = self.check_integration_gate(verification_input)
        if not integration_pass:
            decision = (
                "discard"
                if verification_input.retry_count >= self.max_retry_before_discard
                else "revise"
            )
            feedback = self.build_feedback(integration_failures)
            return VerificationOutput(
                decision=decision,
                reason=integration_failures[0],
                stage=verification_input.stage,
                absolute_metrics={},
                delta_metrics={},
                trigger_stats={
                    "trigger_count": verification_input.trigger_stats.trigger_count,
                    "triggered_matches": verification_input.trigger_stats.triggered_matches,
                    "total_matches": verification_input.trigger_stats.total_matches,
                    "total_turns": verification_input.trigger_stats.total_turns,
                    "trigger_rate": compute_trigger_rate(verification_input.trigger_stats),
                    "state_changed_by_mechanic_count": verification_input.trigger_stats.state_changed_by_mechanic_count,
                    "state_changed_matches": verification_input.trigger_stats.state_changed_matches,
                    "effective_trigger_rate": verification_input.trigger_stats.effective_trigger_rate_by_match(),
                },
                overall_score=0.0,
                relative_score=0.0,
                failure_modes=integration_failures,
                repair_feedback=feedback,
                metadata_for_library={
                    "accepted_stage": None,
                    "failure_mode": integration_failures[0],
                    "hook_location": verification_input.mechanic.hook_location,
                },
            )

        # Stage 2: behavioral gates on absolute metrics
        behavior_pass, behavior_failures, child_abs = self.check_behavioral_gate(verification_input)
        overall_score = compute_overall_score(verification_input.child_metrics)

        if not behavior_pass:
            decision = (
                "discard"
                if verification_input.retry_count >= self.max_retry_before_discard
                else "revise"
            )
            feedback = self.build_feedback(behavior_failures)

            # Compute deltas anyway so the UI can show how the mechanic
            # moved baseline metrics, even though we're rejecting it for
            # a behavioral-gate reason. Helps the user diagnose the failure.
            _delta = compute_delta_metrics(
                verification_input.parent_metrics,
                verification_input.child_metrics,
            )
            _delta_dict_for_display = {
                "delta_playability":  _delta.delta_playability,
                "delta_balance_gap":  _delta.delta_balance_gap,
                "delta_depth":        _delta.delta_depth,
                "delta_decisiveness": _delta.delta_decisiveness,
                "delta_agency":       _delta.delta_agency,
                "delta_coverage":     _delta.delta_coverage,
            }
            # Also compute the aggregate relative score so the UI's
            # "Relative gain vs baseline" line can show a real number even
            # when we reject for a behavioral reason. The number is just
            # the weighted aggregate of the per-metric deltas above and
            # has no influence on the decision (already made).
            _diagnostic_relative_score = compute_relative_score(
                verification_input.parent_metrics,
                verification_input.child_metrics,
                novelty_score=0.0,
            )

            return VerificationOutput(
                decision=decision,
                reason=behavior_failures[0],
                stage=verification_input.stage,
                absolute_metrics=child_abs,
                delta_metrics=_delta_dict_for_display,
                trigger_stats={
                    "trigger_count": verification_input.trigger_stats.trigger_count,
                    "triggered_matches": verification_input.trigger_stats.triggered_matches,
                    "total_matches": verification_input.trigger_stats.total_matches,
                    "total_turns": verification_input.trigger_stats.total_turns,
                    "trigger_rate": compute_trigger_rate(verification_input.trigger_stats),
                    "state_changed_by_mechanic_count": verification_input.trigger_stats.state_changed_by_mechanic_count,
                    "state_changed_matches": verification_input.trigger_stats.state_changed_matches,
                    "effective_trigger_rate": verification_input.trigger_stats.effective_trigger_rate_by_match(),
                },
                overall_score=overall_score,
                relative_score=_diagnostic_relative_score,
                failure_modes=behavior_failures,
                repair_feedback=feedback,
                metadata_for_library={
                    "accepted_stage": None,
                    "failure_mode": behavior_failures[0],
                    "hook_location": verification_input.mechanic.hook_location,
                },
            )

        # Stage 3: relative-gain gate (catches no-op mechanics)
        relative_score, delta_dict, delta_failures = self.evaluate_relative_gain(verification_input)

        if delta_failures:
            decision = (
                "discard"
                if verification_input.retry_count >= self.max_retry_before_discard
                else "revise"
            )
            feedback = self.build_feedback(delta_failures)

            return VerificationOutput(
                decision=decision,
                reason=delta_failures[0],
                stage=verification_input.stage,
                absolute_metrics=child_abs,
                delta_metrics=delta_dict,
                trigger_stats={
                    "trigger_count": verification_input.trigger_stats.trigger_count,
                    "triggered_matches": verification_input.trigger_stats.triggered_matches,
                    "total_matches": verification_input.trigger_stats.total_matches,
                    "total_turns": verification_input.trigger_stats.total_turns,
                    "trigger_rate": compute_trigger_rate(verification_input.trigger_stats),
                    "state_changed_by_mechanic_count": verification_input.trigger_stats.state_changed_by_mechanic_count,
                    "state_changed_matches": verification_input.trigger_stats.state_changed_matches,
                    "effective_trigger_rate": verification_input.trigger_stats.effective_trigger_rate_by_match(),
                },
                overall_score=overall_score,
                relative_score=relative_score,
                failure_modes=delta_failures,
                repair_feedback=feedback,
                metadata_for_library={
                    "accepted_stage": None,
                    "failure_mode": delta_failures[0],
                    "hook_location": verification_input.mechanic.hook_location,
                    "delta_metrics": delta_dict,
                },
            )

        # Stage 4: description-vs-code alignment gate (LLM reviewer)
        align_pass, align_failures, align_issue = self.check_description_alignment_gate(
            verification_input
        )
        if not align_pass:
            decision = (
                "discard"
                if verification_input.retry_count >= self.max_retry_before_discard
                else "revise"
            )
            base_feedback = self.build_feedback(align_failures)
            feedback = (
                f"{base_feedback} Reviewer flagged: {align_issue}"
                if align_issue else base_feedback
            )
            return VerificationOutput(
                decision=decision,
                reason=align_failures[0],
                stage=verification_input.stage,
                absolute_metrics=child_abs,
                delta_metrics=delta_dict,
                trigger_stats={
                    "trigger_count": verification_input.trigger_stats.trigger_count,
                    "triggered_matches": verification_input.trigger_stats.triggered_matches,
                    "total_matches": verification_input.trigger_stats.total_matches,
                    "total_turns": verification_input.trigger_stats.total_turns,
                    "trigger_rate": compute_trigger_rate(verification_input.trigger_stats),
                    "state_changed_by_mechanic_count": verification_input.trigger_stats.state_changed_by_mechanic_count,
                    "state_changed_matches": verification_input.trigger_stats.state_changed_matches,
                    "effective_trigger_rate": verification_input.trigger_stats.effective_trigger_rate_by_match(),
                },
                overall_score=overall_score,
                relative_score=relative_score,
                failure_modes=align_failures,
                repair_feedback=feedback,
                metadata_for_library={
                    "accepted_stage": None,
                    "failure_mode": align_failures[0],
                    "hook_location": verification_input.mechanic.hook_location,
                    "delta_metrics": delta_dict,
                    "alignment_issue": align_issue,
                },
            )

        # All gates passed
        return VerificationOutput(
            decision="accept",
            reason="positive_relative_gain",
            stage=verification_input.stage,
            absolute_metrics=child_abs,
            delta_metrics=delta_dict,
            trigger_stats={
                "trigger_count": verification_input.trigger_stats.trigger_count,
                "triggered_matches": verification_input.trigger_stats.triggered_matches,
                "total_matches": verification_input.trigger_stats.total_matches,
                "total_turns": verification_input.trigger_stats.total_turns,
                "trigger_rate": compute_trigger_rate(verification_input.trigger_stats),
                "state_changed_by_mechanic_count": verification_input.trigger_stats.state_changed_by_mechanic_count,
                "state_changed_matches": verification_input.trigger_stats.state_changed_matches,
                "effective_trigger_rate": verification_input.trigger_stats.effective_trigger_rate_by_match(),
            },
            overall_score=overall_score,
            relative_score=relative_score,
            failure_modes=[],
            repair_feedback=None,
            metadata_for_library={
                "accepted_stage": verification_input.stage,
                "failure_mode": None,
                "hook_location": verification_input.mechanic.hook_location,
                "delta_metrics": delta_dict,
                "absolute_metrics": child_abs,
                "parent_summary": verification_input.parent_summary,
            },
        )
