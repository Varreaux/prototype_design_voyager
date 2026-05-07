"""
morgan_autobucket.py
====================
LLM-as-judge bucketing for the human ranking experiment.

Reads top-20 mechanics from library_cards.json (sorted by aggregate, same
ordering pair-lab uses), generates all 190 pairs, applies tag-based fun
heuristics + per-pair overrides, writes:

  web/data/morgan_ranking.json     — buckets + ranking, the format the
                                     /api/morgan/load endpoint expects.
  web/data/morgan_rationales.json  — per-pair one-line rationale so the
                                     user can spot-check my reasoning.

Run from the project root:

    python3 scripts/morgan_autobucket.py
"""

from __future__ import annotations

import itertools
import json
import os
import time
from typing import Dict, List, Set, Tuple


# ── Configuration ────────────────────────────────────────────────────────────

TOP_N = 20
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY_FILE = os.path.join(PROJECT_ROOT, "library_cards.json")
OUT_RANKING  = os.path.join(PROJECT_ROOT, "web", "data", "morgan_ranking.json")
OUT_RATIONALES = os.path.join(PROJECT_ROOT, "web", "data", "morgan_rationales.json")

BUCKETS = ["very_fun", "fun", "ok", "weak", "not_fun"]


# ── Mechanic tags (manually curated by reading each description) ─────────────
#
# Tags are how I encode my read of each mechanic. The pair-bucket rules
# below combine these into a verdict. Order is identical to top-20 ranking
# from library_cards.json (sorted by aggregate desc, iteration asc).
#
# The names are checked at runtime — if the library order shifts, the
# script errors instead of silently misalignment.

TAGS_BY_NAME: Dict[str, Set[str]] = {
    "cumulative_score_reset_on_exact_match_and_deficit_draw": {
        "match-opp", "score-reset", "comeback-pro", "big-swing",
        "interaction-high", "decision-high",
    },
    "turn_parity_score_boost_revised": {
        "turn-parity", "extra-turn", "always-on", "decision-mid",
        "interaction-low",
    },
    "high_card_hand_swap_improved": {
        "high-card", "swap", "interaction-high", "decision-high",
        "comeback-mild",
    },
    "exact_score_bonus": {
        "mult-of-10", "score-bonus", "always-on", "decision-mid",
        "interaction-low",
    },
    "high_card_risk_reward_extra_turn": {
        "high-card", "extra-turn", "hand-mgmt", "decision-high",
        "risk-reward", "interaction-low",
    },
    "score_difference_card_trade": {
        "swap", "comeback-pro", "conditional-behind", "interaction-high",
        "decision-high",
    },
    "low_card_penalty": {
        "low-card", "always-on", "decision-low", "interaction-low",
        "negative",
    },
    "strategic_hand_depletion_bonus": {
        "endgame", "hand-mgmt", "comeback-pro", "conditional-behind",
        "decision-mid", "interaction-low",
    },
    "cumulative_low_score_defense_bonus": {
        "comeback-pro", "continuous-behind", "interaction-low",
        "decision-low",
    },
    "cumulative_last_played_match_bonus": {
        "match-opp", "decision-high", "interaction-mid", "combo",
    },
    "hand_size_advantage_draw_penalty": {
        "hand-mgmt", "balancing", "decision-mid", "interaction-low",
    },
    "consecutive_same_value_combo_revised": {
        "match-either", "extra-turn", "decision-high", "interaction-mid",
        "big-bonus",
    },
    "card_value_difference_bonus": {
        "match-near", "decision-high", "interaction-mid", "reactive",
    },
    "exact_last_played_mirror_score": {
        "match-opp", "decision-high", "interaction-mid", "reactive",
    },
    "cumulative_score_advantage_bonus": {
        "runaway", "anti-comeback", "decision-low", "interaction-mid",
    },
    "score_difference_hand_draw": {
        "comeback-pro", "draw-card", "conditional-behind",
        "decision-low", "interaction-low",
    },
    "score_milestone_opponent_card_steal": {
        "mult-of-25", "steal", "interaction-high", "milestone",
        "decision-mid",
    },
    "score_multiple_of_five_score_boost": {
        "mult-of-5", "score-bonus", "always-on", "decision-mid",
        "harsh", "interaction-low",
    },
    "score_deficit_hand_replenish": {
        "comeback-pro", "draw-card", "conditional-behind",
        "decision-low", "interaction-low",
    },
    "score_milestone_hand_size_penalty": {
        "anti-runaway", "milestone", "hand-cap", "interaction-low",
        "decision-low",
    },
}


# ── Pair-bucket rules (applied in order; first match wins) ───────────────────
#
# Each rule is (predicate(tags_a, tags_b) -> Optional[(bucket, rationale)]).
# Rules go from most specific to most general. Last rule is the default.

def _has(a: Set[str], b: Set[str], *needles: str) -> bool:
    """True if every needle is present in (a ∪ b)."""
    union = a | b
    return all(n in union for n in needles)


def _both_have(a: Set[str], b: Set[str], tag: str) -> bool:
    return tag in a and tag in b


def _either_has(a: Set[str], b: Set[str], *tags: str) -> bool:
    union = a | b
    return any(t in union for t in tags)


def classify(tags_a: Set[str], tags_b: Set[str]) -> Tuple[str, str]:
    a, b = tags_a, tags_b

    # ── Strong redundancies (penalised most)
    match_count = sum(1 for x in [a, b]
                      if "match-opp" in x or "match-either" in x or "match-near" in x)
    if match_count == 2:
        # Two reactive-on-opponent's-last-card mechanics. Engagement
        # collapses because triggers compete on the same trigger event.
        return ("weak", "both reward matching the opponent's last card — redundant")

    # Two pure score-bonus-on-multiple mechanics → both fire on every
    # play, no real strategic differentiation.
    score_bonus_count = sum(1 for x in [a, b]
                            if "score-bonus" in x and "always-on" in x)
    if score_bonus_count == 2:
        return ("weak", "two always-on multiple-of-N score bonuses — additive but flavorless")

    # Two pro-comeback conditional-behind mechanics → only one ever
    # fires per match (whichever is more lenient), so the other adds
    # nothing.
    behind_count = sum(1 for x in [a, b]
                       if "comeback-pro" in x and "conditional-behind" in x)
    if behind_count == 2:
        return ("weak", "both fire only when behind — overlapping catch-up triggers")

    # Two draw-card comeback mechanics specifically — one strictly
    # subsumes the other.
    if _both_have(a, b, "draw-card") and _both_have(a, b, "comeback-pro"):
        return ("not_fun", "near-duplicate catch-up draws — one always shadows the other")

    # ── Strong synergies (rewarded most)

    # Score-reset + anti-runaway = both fight runaway leaders, the reset
    # is dramatic when triggered, the cap forces the leader to spread thin.
    if _either_has(a, b, "score-reset") and _either_has(a, b, "anti-runaway"):
        return ("very_fun", "score-reset + anti-runaway create dramatic comeback dynamics")

    # Swap + match-opp: opponent state read AND opponent state mutated.
    # Player can engineer matches by manipulating opponent's hand.
    if _either_has(a, b, "swap") and _either_has(a, b, "match-opp", "match-near"):
        return ("very_fun", "swap + reactive bonus — manipulate opponent's hand to set up matches")

    # Steal + match-opp / match-either: again, swap-style + reactive bonus.
    if _either_has(a, b, "steal") and _either_has(a, b, "match-opp", "match-either", "match-near"):
        return ("very_fun", "steal + match-bonus create active set-up gameplay")

    # Score-reset + match (when not already redundant): the reset triggers
    # on match anyway but pair adds another match-bonus → competing
    # incentives on the same trigger creates a real choice.
    if _either_has(a, b, "score-reset") and _either_has(a, b, "match-opp", "match-either", "match-near"):
        return ("fun", "match triggers competing reset/bonus effects — real risk-reward")

    # Push-pull: pro-comeback + anti-comeback → tension throughout.
    if _either_has(a, b, "comeback-pro") and _either_has(a, b, "anti-comeback", "runaway"):
        return ("fun", "pro-comeback + anti-runaway create dynamic push-pull")

    # Two interaction-high mechanics → opponent state matters constantly.
    if _both_have(a, b, "interaction-high"):
        return ("fun", "both mechanics actively manipulate opponent state")

    # Risk-reward + match: high-card play already a risk; adding match
    # creates layered decisions.
    if _either_has(a, b, "risk-reward") and _either_has(a, b, "match-opp", "match-either", "match-near"):
        return ("fun", "high-card risk-reward layered with reactive match-bonus")

    # Extra-turn + match: combo potential — match opponent, get extra
    # turn, can keep stacking.
    if _either_has(a, b, "extra-turn") and _either_has(a, b, "match-opp", "match-either", "match-near"):
        return ("fun", "match-bonus chained with extra-turn enables multi-card combos")

    # ── Negative-without-redemption combinations

    # Anti-runaway + always-on bonus: bonus inflates score quickly,
    # anti-runaway then claps it down. Could be fun, but if the bonus
    # is decision-mid (i.e. obvious), it's just whiplash.
    if (_either_has(a, b, "anti-runaway") and
        _either_has(a, b, "always-on") and
        not _either_has(a, b, "decision-high")):
        return ("weak", "always-on bonus inflates score, anti-runaway whiplashes — feels arbitrary")

    # Two purely self-affecting mechanics with low decision quality.
    if (_both_have(a, b, "interaction-low") and
        ("decision-low" in a) and ("decision-low" in b)):
        return ("not_fun", "no meaningful choices, neither touches the opponent")

    # One mechanic that's just "play a small card" punishment paired with
    # something else equally generic.
    if _either_has(a, b, "negative") and _either_has(a, b, "interaction-low") and not _either_has(a, b, "interaction-high"):
        # This is a softer rule — either rule above will already have
        # caught a lot of weak cases. Reserve "not_fun" for genuine
        # bottom dwellers.
        if not (_either_has(a, b, "decision-high") or _either_has(a, b, "comeback-pro")):
            return ("weak", "low-card penalty plus a non-interactive partner — flat play")

    # ── Solid/positive combinations

    # Either contains decision-high + the other reads opponent state.
    if _either_has(a, b, "decision-high") and _either_has(a, b, "interaction-mid", "interaction-high"):
        return ("fun", "real decisions paired with opponent-state interaction")

    # Anti-runaway + comeback-pro (without already-classified push-pull):
    # the cap-and-replenish rhythm is good for fun.
    if _either_has(a, b, "anti-runaway") and _either_has(a, b, "comeback-pro"):
        return ("fun", "anti-runaway cap with catch-up support keeps games competitive")

    # Two extra-turn mechanics: combo-chain potential.
    if _both_have(a, b, "extra-turn"):
        return ("fun", "two extra-turn mechanics enable extended combo turns")

    # Hand-management + score-reset: forced rebuild after reset is dramatic.
    if _either_has(a, b, "score-reset") and _either_has(a, b, "hand-mgmt", "anti-runaway", "hand-cap"):
        return ("fun", "score-reset compounded by hand pressure")

    # Steal alone with anything else interactive — opponent feels every move.
    if _either_has(a, b, "steal") and _either_has(a, b, "interaction-high", "interaction-mid"):
        return ("fun", "card-stealing keeps both players engaged with each other")

    # ── Default

    return ("ok", "mechanically sound, no obvious synergy or redundancy from descriptions")


# ── Per-pair overrides ──────────────────────────────────────────────────────
#
# Cases where rules would mis-judge from the descriptions alone. Keys are
# (sorted_name_a, sorted_name_b) tuples. Empty by default — fill in as we
# spot anomalies on review.

PAIR_OVERRIDES: Dict[Tuple[str, str], Tuple[str, str]] = {
    # cumulative_score_reset_on_exact_match_and_deficit_draw with
    # cumulative_last_played_match_bonus / exact_last_played_mirror_score:
    # the "score-reset triggers on match" specifically PUNISHES the same
    # action the bonus rewards. Real risk-reward, very fun.
    (("cumulative_last_played_match_bonus",
      "cumulative_score_reset_on_exact_match_and_deficit_draw")):
        ("very_fun",
         "match triggers both bonus AND reset — every match decision is high-stakes"),
    (("cumulative_score_reset_on_exact_match_and_deficit_draw",
      "exact_last_played_mirror_score")):
        ("very_fun",
         "match triggers both bonus AND reset — every match decision is high-stakes"),
    (("consecutive_same_value_combo_revised",
      "cumulative_score_reset_on_exact_match_and_deficit_draw")):
        ("very_fun",
         "match-anyone bonus AND opponent-match reset — overlapping triggers create tension"),

    # high_card_hand_swap_improved + high_card_risk_reward_extra_turn:
    # both reward 9/10 plays. Layered: swap restructures hands, risk-
    # reward gives extra turn. Genuinely synergistic for high-card builds.
    (("high_card_hand_swap_improved",
      "high_card_risk_reward_extra_turn")):
        ("very_fun",
         "two complementary high-card payoffs build an aggressive strategy"),

    # cumulative_score_reset + score_milestone_hand_size_penalty:
    # both punish leaders. Reset is volatile, hand-cap is grindy. Together
    # they make leading actively hostile — comebacks become the norm.
    (("cumulative_score_reset_on_exact_match_and_deficit_draw",
      "score_milestone_hand_size_penalty")):
        ("very_fun",
         "two anti-leader mechanics turn every comfortable lead into a trap"),

    # cumulative_score_advantage_bonus + score_milestone_hand_size_penalty:
    # one rewards being ahead, the other punishes scoring high. Direct
    # conflict — leader becomes bonkers strong then suddenly handicapped.
    (("cumulative_score_advantage_bonus",
      "score_milestone_hand_size_penalty")):
        ("fun",
         "anti-runaway penalty conflicts with runaway bonus — chaotic see-saw"),

    # low_card_penalty + score_multiple_of_five_score_boost:
    # both are always-on score modifiers, both push the player toward
    # specific card values. Mostly decision-mid, mild interaction.
    (("low_card_penalty",
      "score_multiple_of_five_score_boost")):
        ("weak",
         "two always-on modifiers — additive math, no real strategic depth"),

    # cumulative_low_score_defense_bonus + cumulative_score_advantage_bonus:
    # one rewards being behind, the other rewards being ahead — direct
    # tension, but both are continuous so the game is largely decided by
    # who's currently leading. Decent but not standout.
    (("cumulative_low_score_defense_bonus",
      "cumulative_score_advantage_bonus")):
        ("fun",
         "continuous push-pull — losing player gains, leader pulls further ahead"),
}


# ── Pair-id helpers ──────────────────────────────────────────────────────────

def pair_id_for(name_a: str, name_b: str) -> str:
    return "|".join(sorted((name_a, name_b)))


def override_key(name_a: str, name_b: str) -> Tuple[str, str]:
    a, b = sorted((name_a, name_b))
    return (a, b)


# ── Driver ──────────────────────────────────────────────────────────────────

def main() -> None:
    cards = json.load(open(LIBRARY_FILE))
    card = [c for c in cards if c.get("game_type") == "card"]
    card.sort(key=lambda c: (
        -c.get("scores", {}).get("aggregate", 0.0),
        c.get("iteration", 10_000),
    ))
    top = card[:TOP_N]
    if len(top) < TOP_N:
        raise SystemExit(f"Only {len(top)} card mechanics — need {TOP_N}")

    names = [c["mechanic_name"] for c in top]

    # Sanity-check that every mechanic in the top-20 has tags assigned.
    missing = [n for n in names if n not in TAGS_BY_NAME]
    if missing:
        raise SystemExit(
            "Missing tag entries for these mechanics (update TAGS_BY_NAME):\n"
            + "\n".join(f"  - {n}" for n in missing)
        )

    snapshot = []
    buckets: Dict[str, str] = {}
    rationales: Dict[str, str] = {}

    pair_indices = list(itertools.combinations(range(TOP_N), 2))
    for i, j in pair_indices:
        ni, nj = names[i], names[j]
        pid = pair_id_for(ni, nj)
        ovr = PAIR_OVERRIDES.get(override_key(ni, nj))
        if ovr is not None:
            bucket, rationale = ovr
        else:
            bucket, rationale = classify(TAGS_BY_NAME[ni], TAGS_BY_NAME[nj])
        buckets[pid] = bucket
        rationales[pid] = rationale
        snapshot.append({
            "pair_id":    pid,
            "names":      sorted([ni, nj]),
            # We don't have metric vectors on disk (the user's pair-lab
            # results live in browser localStorage). Snapshot still has
            # to satisfy the morgan endpoint schema, so include empty
            # placeholders the fitness trainer can fill in later.
            "components": {},
            "composite":  0.0,
            "summary":    {},
        })

    # Order: very_fun → fun → ok → weak → not_fun, alphabetical within tier.
    bucket_rank = {b: i for i, b in enumerate(BUCKETS)}
    snapshot.sort(key=lambda p: (bucket_rank[buckets[p["pair_id"]]], p["pair_id"]))
    ranking = [p["pair_id"] for p in snapshot]

    record = {
        "snapshot": snapshot,
        "ranking":  ranking,
        "buckets":  buckets,
        "saved_at": time.time(),
        "source":   "morgan_autobucket.py (LLM-as-judge)",
    }

    os.makedirs(os.path.dirname(OUT_RANKING), exist_ok=True)
    # Atomic write.
    tmp = OUT_RANKING + ".tmp"
    with open(tmp, "w") as f:
        json.dump(record, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUT_RANKING)

    rat_record = {
        "saved_at":  time.time(),
        "source":    "morgan_autobucket.py (LLM-as-judge)",
        "rationale_by_pair": rationales,
    }
    tmp = OUT_RATIONALES + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rat_record, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUT_RATIONALES)

    # Distribution summary for the user.
    counts: Dict[str, int] = {b: 0 for b in BUCKETS}
    for v in buckets.values():
        counts[v] = counts.get(v, 0) + 1
    print(f"Wrote {len(buckets)} pairs to {OUT_RANKING}")
    print(f"Wrote {len(rationales)} rationales to {OUT_RATIONALES}")
    print()
    print("Bucket distribution:")
    for b in BUCKETS:
        n = counts[b]
        pct = 100.0 * n / len(buckets) if buckets else 0.0
        print(f"  {b:9s}: {n:3d}  ({pct:5.1f}%)")


if __name__ == "__main__":
    main()
