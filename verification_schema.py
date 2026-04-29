"""
verification_schema.py
======================
DesignVoyager — Verification dataclasses.

Adapted from the team's repo (Cody's verification_schema.py).
Defines the type contracts for inputs/outputs of the SelfVerifier:
mechanic candidate, integration report, playtest metrics for both the
parent (no mechanic) and child (with mechanic) games, trigger stats,
delta metrics, and the verification input/output bundles.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class MechanicCandidate:
    """
    Output from the proposal module. Maps onto the mechanic dict that
    proposal_module.propose_mechanic() returns.
    """
    mechanic_name: str
    mechanic_type: str
    description: str
    python_code: str
    justification: str = ""
    hook_location: str = "perform_move"


@dataclass
class IntegrationReport:
    """
    Reports whether the proposed mechanic can actually be integrated into
    the game implementation. In Morgan's pipeline, compile_check produces
    a (bool, error) pair; we synthesize a full IntegrationReport from it.
    """
    schema_ok: bool
    syntax_ok: bool
    hook_ok: bool
    instantiation_ok: bool
    dry_run_ok: bool
    error_message: str = ""


@dataclass
class PlaytestMetrics:
    """
    Metrics for one concrete game configuration: either the parent game
    (without the new mechanic, the "baseline") or the child game (with
    the new mechanic).
    """
    total_matches: int
    completed_matches: int

    # Symmetric fairness (equal-strength MCTS vs MCTS)
    p1_win_rate: float
    p2_win_rate: float

    # Strong-vs-weak depth proxy
    strong_agent_win_rate: float
    weak_agent_win_rate: float

    # Optional diagnostics
    draw_rate: float = 0.0
    avg_game_length: float = 0.0
    avg_legal_actions: float = 0.0
    multi_choice_turns: int = 0
    total_turns: int = 0
    covered_cells: int = 0
    board_cell_count: int = 0


@dataclass
class TriggerStats:
    """
    Tracks whether the mechanic was actually used during gameplay.
    trigger_count = number of times the mechanic function was invoked.
    triggered_matches = matches where the mechanic ran at least once.
    state_changed_by_mechanic_count = total turns the mechanic actually
        modified the state dict (vs being a no-op pass-through).
    state_changed_matches = matches where the mechanic actually changed
        state at least once. This is the honest "trigger rate" from a game
        designer's perspective; trigger_count / triggered_matches are pinned
        at the maximum because perform_move calls the mechanic_fn every turn
        regardless of whether the mechanic's condition fired.
    """
    trigger_count: int
    triggered_matches: int
    total_matches: int
    total_turns: int
    state_changed_by_mechanic_count: int = 0
    state_changed_matches: int = 0

    def trigger_rate_by_match(self) -> float:
        if self.total_matches <= 0:
            return 0.0
        return self.triggered_matches / self.total_matches

    def trigger_rate_by_turn(self) -> float:
        if self.total_turns <= 0:
            return 0.0
        return self.trigger_count / self.total_turns

    def effective_trigger_rate_by_match(self) -> float:
        """Fraction of matches where the mechanic actually changed state."""
        if self.total_matches <= 0:
            return 0.0
        return self.state_changed_matches / self.total_matches


@dataclass
class DeltaMetrics:
    delta_playability: float
    delta_balance_gap: float
    delta_depth: float
    delta_decisiveness: float
    delta_agency: float
    delta_coverage: float = 0.0


@dataclass
class VerificationInput:
    """
    Full input to the verifier.
    """
    stage: int
    mechanic: MechanicCandidate
    integration: IntegrationReport
    parent_metrics: PlaytestMetrics
    child_metrics: PlaytestMetrics
    trigger_stats: TriggerStats
    retry_count: int = 0
    parent_summary: str = ""
    # "board" or "card", used by the description-vs-code alignment gate
    # to give the reviewer model the right perform_move context note.
    game_name: str = "board"


@dataclass
class VerificationOutput:
    decision: str                    # accept / revise / discard
    reason: str
    stage: int

    absolute_metrics: Dict[str, Any]
    delta_metrics: Dict[str, Any]
    trigger_stats: Dict[str, Any]

    overall_score: float
    relative_score: float

    failure_modes: List[str] = field(default_factory=list)
    repair_feedback: Optional[str] = None

    metadata_for_library: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
