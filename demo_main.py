"""
demo_main.py
============
DesignVoyager — Presentation-ready animated terminal.

Same logic as main.py, but with:
  - Spinning indicators while GPT-4 thinks
  - Animated progress bar during playtesting
  - Color-coded score bars (green / yellow / red)
  - Big bordered verdict panels (ACCEPTED / REVISING / DISCARDED)
  - Summary table at the end

Run with:
    python3 demo_main.py
    python3 demo_main.py --game card      # use the card game
    python3 demo_main.py --iterations 5
"""

import argparse
import contextlib
import io

from dotenv import load_dotenv

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from base_game import BaseGame
from card_game import CardGame
from mechanic_library import MechanicLibrary
from proposal_module import propose_mechanic
from compile_check import compile_check
from playtest_module import run_baseline, run_playtest_full
from verification_module import verify, ACCEPT, REVISE, DISCARD, MIN_PLAYABILITY
from curriculum import Curriculum
import discarded_library

load_dotenv()

# ── Game registry ─────────────────────────────────────────────────────────────
GAME_REGISTRY = {
    'board': (BaseGame, 'library.json',      'discarded_board.json'),
    'card':  (CardGame, 'library_card.json', 'discarded_card.json'),
}
DEFAULT_GAME = 'board'

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def suppress():
    """
    Silence the print() statements inside the underlying modules
    so only our rich output shows.  rich.Console holds a reference
    to the real stdout object, so it is unaffected by this redirect.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def score_bar(value: float, width: int = 26) -> str:
    """Return a coloured unicode block bar for a 0–1 value."""
    filled = int(value * width)
    bar    = "█" * filled + "░" * (width - filled)
    color  = "green" if value >= 0.75 else ("yellow" if value >= 0.50 else "red")
    return f"[{color}]{bar}[/{color}]  [bold]{value:.2f}[/bold]"


def print_scores(scores: dict):
    balance = round(1 - scores["balance_gap"], 2)
    # Playability is a binary gate — show pass/fail based on the actual score
    playability = scores.get("playability", 0)
    if playability >= MIN_PLAYABILITY:
        console.print(f"  [dim]Playability gate[/dim]  [bold green]✓  passed[/bold green]")
    else:
        console.print(
            f"  [dim]Playability gate[/dim]  [bold red]✗  failed[/bold red]"
            f"  [dim]({playability:.0%} of games finished, need 100%)[/dim]"
        )
    t = Table(box=None, show_header=False, padding=(0, 2))
    t.add_column(style="dim", width=14, no_wrap=True)
    t.add_column(no_wrap=True)
    t.add_row("Balance",   score_bar(balance))
    t.add_row("Depth",     score_bar(scores["depth"]))
    t.add_row("─" * 12,   "─" * 34)
    t.add_row("Aggregate", score_bar(scores["aggregate"]))
    console.print(t)


def print_verdict(decision: str, name: str, scores: dict = None):
    if decision == ACCEPT:
        agg = f"\n  [dim]aggregate score:  [bold]{scores['aggregate']:.2f}[/bold][/dim]" if scores else ""
        console.print(Panel(
            f"  [bold green]{name}[/bold green]{agg}",
            title="  ✓   ACCEPTED  ",
            border_style="bright_green",
            padding=(1, 4),
        ))
    elif decision == REVISE:
        console.print(Panel(
            f"  [yellow]{name}[/yellow]\n  [dim]Sending feedback to GPT-4 for one revision attempt...[/dim]",
            title="  →   REVISING  ",
            border_style="yellow",
            padding=(1, 4),
        ))
    else:
        console.print(Panel(
            f"  [red]{name}[/red]\n  [dim]Could not produce a working mechanic.[/dim]",
            title="  ✗   DISCARDED  ",
            border_style="red",
            padding=(1, 4),
        ))


# ── Core step: compile → playtest → verify ────────────────────────────────────

def _compile_playtest_verify(mechanic: dict, already_revised: bool,
                             game_class=None, dummy_state: dict = None,
                             baseline_metrics=None, stage: int = 1) -> tuple:
    """
    Returns (decision, scores).
    Prints its own rich output for each sub-step.

    Args:
        game_class       : GameInterface subclass for playtesting
        dummy_state      : game-specific dummy state for compile checking
        baseline_metrics : PlaytestMetrics for the no-mechanic baseline
        stage            : current curriculum stage (1, 2, or 3)
    """
    # Compile check
    console.print("  [dim]Compile check[/dim]", end="  ")
    with suppress():
        ok, error = compile_check(mechanic, dummy_state=dummy_state)

    if not ok:
        console.print("[bold red]✗  failed[/bold red]")
        console.print(f"  [red dim]{error[:90]}[/red dim]\n")
        if already_revised:
            return DISCARD, {}
        mechanic["_revision_feedback"] = f"The code crashed: {error}. Please rewrite to fix this."
        return REVISE, {}

    console.print("[bold green]✓  passed[/bold green]")

    # Playtest (full, with trigger tracking)
    with console.status(
        f"  [cyan]Playtesting [bold]{mechanic['mechanic_name']}[/bold]"
        f" — running automated games...[/cyan]"
    ):
        with suppress():
            child_metrics, trigger_stats, scores = run_playtest_full(
                mechanic, game_class=game_class)

    print_scores(scores)
    trigger_pct = trigger_stats.trigger_rate_by_match()
    console.print(f"  [dim]Trigger rate[/dim]      [bold]{trigger_pct:.0%}[/bold]"
                  f"  [dim]({trigger_stats.triggered_matches}/{trigger_stats.total_matches} matches)[/dim]")

    # Verify (delta-gated)
    with suppress():
        decision, feedback, output = verify(
            mechanic, child_metrics,
            parent_metrics=baseline_metrics,
            trigger_stats=trigger_stats,
            compile_ok=True,
            stage=stage,
            already_revised=already_revised,
        )
    if decision == REVISE:
        mechanic["_revision_feedback"] = feedback

    # Show the relative-score line so the user can see why a mechanic was
    # accepted or rejected even when its absolute scores look fine.
    rel = output.get("relative_score", 0.0) if output else 0.0
    stage_thr = 0.015 if stage <= 1 else (0.012 if stage == 2 else 0.008)
    rel_color = "green" if rel >= stage_thr else ("yellow" if rel >= 0 else "red")
    console.print(f"  [dim]Relative gain[/dim]     "
                  f"[{rel_color}]{rel:+.3f}[/{rel_color}]"
                  f"  [dim]vs baseline (threshold {stage_thr:.3f})[/dim]")

    return decision, scores


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_loop(n_iterations: int = 3, top_k: int = 3, game_name: str = DEFAULT_GAME):
    game_class, library_file, discarded_file = GAME_REGISTRY[game_name]

    # Instantiate a throw-away game object to get descriptions and dummy state
    _dummy_game = game_class.create()
    skeleton    = _dummy_game.get_skeleton_description()
    state_desc  = _dummy_game.get_state_description()
    dummy_state = _dummy_game.get_dummy_state()

    with suppress():
        library = MechanicLibrary(filepath=library_file)
    curriculum = Curriculum()

    banned_names   = discarded_library.load(discarded_file)
    tried_this_run = set()

    # ── Welcome banner ─────────────────────────────────────────────────────
    console.print()
    console.print(Panel(
        f"[bold cyan]DesignVoyager[/bold cyan]  [dim]—  Autonomous Game Mechanic Designer[/dim]\n\n"
        f"  Game        [bold white]{game_name}[/bold white]  [dim]({game_class.__name__})[/dim]\n"
        f"  Iterations  [bold white]{n_iterations}[/bold white]     "
        f"Context k  [bold white]{top_k}[/bold white]     "
        f"Library  [bold cyan]{library.size()} mechanics[/bold cyan]\n"
        f"  Curriculum  [yellow]{curriculum.progress_str()}[/yellow]\n"
        f"  Banned      [dim]{len(banned_names)} previously discarded mechanics[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))

    # ── Baseline playtest (no mechanic) ────────────────────────────────────
    with console.status(
        "  [cyan]Running baseline playtest with no mechanic "
        "(needed for delta-gated verification)...[/cyan]"
    ):
        with suppress():
            baseline_metrics = run_baseline(game_class=game_class)
    bal_pct = baseline_metrics.completed_matches / max(baseline_metrics.total_matches, 1)
    bal_p1  = baseline_metrics.p1_win_rate
    dpth    = baseline_metrics.strong_agent_win_rate - baseline_metrics.weak_agent_win_rate
    console.print(
        f"  [dim]Baseline:[/dim]  "
        f"playability=[bold]{bal_pct:.0%}[/bold]  "
        f"p1 win rate=[bold]{bal_p1:.0%}[/bold]  "
        f"strong-vs-weak gap=[bold]{dpth:+.2f}[/bold]\n"
    )

    accepted_count  = 0
    discarded_count = 0

    for iteration in range(1, n_iterations + 1):
        console.print()
        console.rule(
            f"[bold white]  Iteration {iteration} of {n_iterations}[/bold white]"
            f"  [dim]|  {curriculum.stage_name()}[/dim]"
        )
        console.print()

        # ── Step 1: Retrieve ────────────────────────────────────────────────
        retrieval_query = f"{skeleton} {curriculum.stage_name()}"
        with suppress():
            retrieved = library.retrieve(k=top_k, query=retrieval_query)

        if retrieved:
            names = ", ".join(m["mechanic_name"] for m in retrieved)
            console.print(f"  [dim]Context from library:[/dim] [cyan]{names}[/cyan]\n")
        else:
            console.print(f"  [dim]No library context yet — Gemini starts from scratch.[/dim]\n")

        # ── Step 2: Propose ─────────────────────────────────────────────────
        all_banned = list(set(banned_names) | tried_this_run)
        with console.status(
            f"  [cyan]Gemini is designing a new mechanic  "
            f"[dim](using {len(retrieved)} existing mechanics as context)[/dim]...[/cyan]"
        ):
            with suppress():
                mechanic = propose_mechanic(skeleton, retrieved,
                                            stage_prompt=curriculum.stage_prompt(),
                                            state_description=state_desc,
                                            banned_names=all_banned)

        if mechanic is None:
            print_verdict(DISCARD, "—")
            curriculum.on_discard()
            discarded_count += 1
            continue

        tried_this_run.add(mechanic.get("mechanic_name", ""))

        console.print(
            f"\n  [cyan bold]Proposed:[/cyan bold]  "
            f"[bold white]{mechanic['mechanic_name']}[/bold white]  "
            f"[dim]({mechanic.get('mechanic_type', '')})[/dim]\n"
            f"  [dim]{mechanic.get('description', '')}[/dim]\n"
        )

        # ── Steps 3–5: Compile → Playtest → Verify ─────────────────────────
        decision, scores = _compile_playtest_verify(
            mechanic, already_revised=False,
            game_class=game_class, dummy_state=dummy_state,
            baseline_metrics=baseline_metrics, stage=curriculum.stage,
        )

        # ── Revision path ───────────────────────────────────────────────────
        if decision == REVISE:
            print_verdict(REVISE, mechanic["mechanic_name"])
            console.print()

            feedback        = mechanic.get("_revision_feedback", "Please improve this mechanic.")
            revision_ctx    = retrieved + [{
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

            with console.status(
                f"  [yellow]Gemini is revising "
                f"[bold]{mechanic['mechanic_name']}[/bold]...[/yellow]"
            ):
                with suppress():
                    revised = propose_mechanic(revised_skeleton, revision_ctx,
                                               stage_prompt=curriculum.stage_prompt(),
                                               state_description=state_desc,
                                               banned_names=all_banned)

            if revised is None:
                print_verdict(DISCARD, mechanic["mechanic_name"])
                discarded_library.save_name(mechanic.get("mechanic_name", ""), discarded_file)
                banned_names.append(mechanic.get("mechanic_name", ""))
                curriculum.on_discard()
                discarded_count += 1
                continue

            tried_this_run.add(revised.get("mechanic_name", ""))
            mechanic = revised
            console.print(
                f"\n  [yellow bold]Revised:[/yellow bold]  "
                f"[bold white]{mechanic['mechanic_name']}[/bold white]\n"
            )
            decision, scores = _compile_playtest_verify(
                mechanic, already_revised=True,
                game_class=game_class, dummy_state=dummy_state,
                baseline_metrics=baseline_metrics, stage=curriculum.stage,
            )

        # ── Verdict ─────────────────────────────────────────────────────────
        console.print()
        print_verdict(decision, mechanic["mechanic_name"], scores if decision == ACCEPT else None)
        console.print()

        if decision == ACCEPT:
            with suppress():
                library.add(mechanic, scores, iteration=iteration)
            advanced = curriculum.on_accept()
            accepted_count += 1
            if advanced:
                console.print(Panel(
                    f"  [bold yellow]★  Unlocked {curriculum.stage_name()}[/bold yellow]\n"
                    f"  [dim]Gemini will now propose more complex mechanics.[/dim]",
                    border_style="yellow",
                    padding=(1, 4),
                ))
        else:
            discarded_library.save_name(mechanic.get("mechanic_name", ""), discarded_file)
            banned_names.append(mechanic.get("mechanic_name", ""))
            curriculum.on_discard()
            discarded_count += 1

    # ── Final summary ──────────────────────────────────────────────────────
    summary = Table(
        title="\n  Run Complete",
        box=box.ROUNDED,
        border_style="cyan",
        padding=(0, 4),
        show_header=False,
    )
    summary.add_column(style="dim", width=14)
    summary.add_column(justify="right")
    summary.add_row("Accepted",  f"[bold green]{accepted_count}[/bold green]")
    summary.add_row("Discarded", f"[red]{discarded_count}[/red]")
    summary.add_row("Library",
        f"[bold cyan]{library.size()} mechanics[/bold cyan]  "
        + f"[dim]({', '.join(m['mechanic_name'] for m in library.mechanics)})[/dim]"
        if library.size() else "[dim]empty[/dim]"
    )
    console.print(summary)
    console.print()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DesignVoyager — animated demo mode")
    parser.add_argument("--iterations", "-n", type=int, default=3)
    parser.add_argument("--top-k",      "-k", type=int, default=3)
    parser.add_argument(
        "--game", "-g",
        type=str,
        choices=list(GAME_REGISTRY.keys()),
        default=DEFAULT_GAME,
        help=f"Which game to design mechanics for (default: {DEFAULT_GAME})"
    )
    args = parser.parse_args()
    run_loop(n_iterations=args.iterations, top_k=args.top_k, game_name=args.game)
