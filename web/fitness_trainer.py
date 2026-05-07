"""
fitness_trainer.py
==================
Train a fitness function over the eight pair-lab metrics that predicts
the human bucket label for a pair, and emit headline numbers we can use
in the paper.

Design choices made earlier in the project:
  * Train on bucket labels (very_fun / fun / ok / weak / not_fun), not on
    strict ranking — within-bucket order is essentially noise from a
    single human ranker.
  * Evaluate on BOTH bucket accuracy (exact + ±1) and rank correlation
    (Spearman, Kendall tau on the predicted continuous score) so we get
    the "model agrees on bucket 95% but exact rank 65%" framing.
  * Standardise features so the learned weights are comparable across
    metrics — useful for plotting which dimensions of "fun" the model
    picked up.
  * Linear regression over a 5-level ordinal target is the right tool at
    this scale (190 labels, 8 features). Logistic regression with five
    nominal classes throws away the ordinality. sklearn isn't a hard
    dependency of this project so we stick with numpy least-squares.

Inputs
------
The trainer needs two things keyed by `pair_id`:
  1. Bucket labels — read from `web/data/ranking.json` or supplied
     directly via `train_from_dicts`.
  2. Metric vectors — pulled from the bucket file's snapshot if present;
     otherwise from a fallback snapshot (e.g. `web/data/ranking.json`
     written by Human Ranking's Save).

Output
------
`web/data/fitness.json` with weights, intercept, accuracies, rank
correlation, per-pair predictions, and the input-distribution sanity
checks (number of pairs, bucket counts).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ── Constants ───────────────────────────────────────────────────────────────

BUCKETS = ["very_fun", "fun", "ok", "weak", "not_fun"]
# Higher score = more fun. Used both as the regression target and as
# a way to convert predicted scores back to bucket labels.
BUCKET_TO_SCORE = {b: 4 - i for i, b in enumerate(BUCKETS)}
SCORE_TO_BUCKET = {v: k for k, v in BUCKET_TO_SCORE.items()}

# The eight pair-lab features, in a fixed order. The order matters because
# the weight vector is positional in the output. New metrics added to
# pair_eval should be appended here — never inserted in the middle.
FEATURE_NAMES = [
    "balance", "decisiveness", "both_meaningful", "length_sanity",
    "volatility", "length_cv", "joint_fire_rate", "non_greedy_rate",
]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _vec_from_components(components: dict) -> Optional[List[float]]:
    """
    Pull the 8-feature vector out of a pair-lab `components` dict.
    Returns None if any feature is missing — the caller should drop the
    pair rather than impute.
    """
    if not isinstance(components, dict):
        return None
    out: List[float] = []
    for f in FEATURE_NAMES:
        v = components.get(f)
        if v is None:
            return None
        out.append(float(v))
    return out


def _standardise(X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Zero-mean, unit-variance standardisation. Returns (X_std, mu, sigma)."""
    mu = X.mean(axis=0)
    sigma = X.std(axis=0)
    sigma = np.where(sigma > 1e-9, sigma, 1.0)   # avoid divide-by-zero
    return (X - mu) / sigma, mu, sigma


def _kendall_tau(a: np.ndarray, b: np.ndarray) -> float:
    """
    Kendall tau-b correlation. Plain O(n²) implementation — fine at 190
    points (~36k pair comparisons). Adjusts for ties on either side.
    """
    n = len(a)
    if n < 2:
        return 0.0
    concordant = discordant = 0
    ties_a = ties_b = 0
    for i in range(n):
        for j in range(i + 1, n):
            da = a[i] - a[j]
            db = b[i] - b[j]
            if da == 0 and db == 0:
                continue
            if da == 0:
                ties_a += 1
                continue
            if db == 0:
                ties_b += 1
                continue
            if (da > 0) == (db > 0):
                concordant += 1
            else:
                discordant += 1
    n0 = (concordant + discordant + ties_a) * (concordant + discordant + ties_b)
    if n0 == 0:
        return 0.0
    return (concordant - discordant) / np.sqrt(n0)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman rank correlation = Pearson on rank-transformed data."""
    if len(a) < 2:
        return 0.0
    ra = _rankdata(a)
    rb = _rankdata(b)
    rho = np.corrcoef(ra, rb)[0, 1]
    return 0.0 if np.isnan(rho) else float(rho)


def _rankdata(x: np.ndarray) -> np.ndarray:
    """Average-rank for ties. 1-indexed, like scipy.stats.rankdata."""
    n = len(x)
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and x[order[j + 1]] == x[order[i]]:
            j += 1
        avg = 0.5 * (i + j) + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


# ── Core training routine ───────────────────────────────────────────────────

def train_from_dicts(buckets: Dict[str, str],
                     metrics_by_id: Dict[str, List[float]]) -> Dict[str, Any]:
    """
    Run the regression and produce the result dict that the dashboard
    will render. `metrics_by_id[pair_id]` must already be a list of
    floats in FEATURE_NAMES order.
    """
    # Join — pair_ids that lack metrics or buckets get dropped, with the
    # drop count surfaced in the result.
    pair_ids: List[str] = []
    rows: List[List[float]] = []
    targets: List[int] = []
    dropped_no_metrics: List[str] = []
    dropped_unknown_bucket: List[str] = []

    for pid, bucket_label in buckets.items():
        if bucket_label not in BUCKET_TO_SCORE:
            dropped_unknown_bucket.append(pid)
            continue
        vec = metrics_by_id.get(pid)
        if vec is None:
            dropped_no_metrics.append(pid)
            continue
        pair_ids.append(pid)
        rows.append(vec)
        targets.append(BUCKET_TO_SCORE[bucket_label])

    if len(pair_ids) < 5:
        return {
            "ok": False,
            "error": (f"Not enough usable pairs ({len(pair_ids)}). "
                      f"Need metric vectors and bucket labels for at "
                      f"least 5 pairs."),
            "dropped_no_metrics": len(dropped_no_metrics),
            "dropped_unknown_bucket": len(dropped_unknown_bucket),
        }

    X = np.array(rows, dtype=float)
    y = np.array(targets, dtype=float)

    X_std, mu, sigma = _standardise(X)

    # Append intercept column; solve via least-squares.
    X_aug = np.column_stack([X_std, np.ones(len(y))])
    beta, *_ = np.linalg.lstsq(X_aug, y, rcond=None)
    weights_std = beta[:-1]
    intercept_std = float(beta[-1])

    # Convert standardised weights back to raw-scale weights so the
    # learned model can be applied to fresh metric vectors without first
    # standardising them. raw_w = std_w / sigma; raw_b = b_std - sum(std_w * mu / sigma)
    weights_raw = weights_std / sigma
    intercept_raw = intercept_std - float(np.sum(weights_raw * mu))

    # Predict on the training set.
    preds = X @ weights_raw + intercept_raw
    pred_bucket_idx = np.clip(np.round(preds), 0, 4).astype(int)
    true_bucket_idx = y.astype(int)

    exact = float(np.mean(pred_bucket_idx == true_bucket_idx))
    within_one = float(np.mean(np.abs(pred_bucket_idx - true_bucket_idx) <= 1))

    spearman = _spearman(preds, y)
    kendall = _kendall_tau(preds, y)

    # Per-pair record sorted descending by predicted fitness.
    per_pair = []
    for i, pid in enumerate(pair_ids):
        pred_b_score = int(pred_bucket_idx[i])
        per_pair.append({
            "pair_id":          pid,
            "human_bucket":     SCORE_TO_BUCKET[int(true_bucket_idx[i])],
            "predicted_bucket": SCORE_TO_BUCKET[pred_b_score],
            "predicted_score":  float(preds[i]),
            "agreement":        bool(pred_bucket_idx[i] == true_bucket_idx[i]),
            "off_by":           int(pred_bucket_idx[i] - true_bucket_idx[i]),
        })
    per_pair.sort(key=lambda r: -r["predicted_score"])

    # Bucket-count distribution (helps spot a degenerate label set early).
    bucket_counts = {b: 0 for b in BUCKETS}
    for pid in pair_ids:
        bucket_counts[buckets[pid]] = bucket_counts.get(buckets[pid], 0) + 1

    # Confusion matrix: true bucket × predicted bucket.
    # The bucket_idx values are score-indexed (0 = not_fun, 4 = very_fun)
    # but the UI renders rows/cols in display order (very_fun first), so
    # we flip both axes once before emitting. Otherwise the top-left cell
    # in the rendered matrix shows the not_fun→not_fun count under a
    # very_fun→very_fun label.
    conf_score = [[0] * 5 for _ in range(5)]
    for t, p in zip(true_bucket_idx, pred_bucket_idx):
        conf_score[int(t)][int(p)] += 1
    conf = [[conf_score[4 - r][4 - c] for c in range(5)] for r in range(5)]

    return {
        "ok":            True,
        "feature_names": FEATURE_NAMES,
        # Standardised weights are interpretable as relative importance
        # (zero-mean / unit-variance features, so a weight of 0.5 means
        # one standard deviation in this metric shifts predicted bucket
        # score by 0.5 — half a bucket).
        "weights_standardised": {
            f: float(w) for f, w in zip(FEATURE_NAMES, weights_std)
        },
        "intercept_standardised": intercept_std,
        # Raw-scale weights apply directly to fresh pair-lab vectors.
        "weights_raw": {
            f: float(w) for f, w in zip(FEATURE_NAMES, weights_raw)
        },
        "intercept_raw":   intercept_raw,
        "feature_means":   {f: float(m) for f, m in zip(FEATURE_NAMES, mu)},
        "feature_stds":    {f: float(s) for f, s in zip(FEATURE_NAMES, sigma)},
        "n_pairs":         len(pair_ids),
        "bucket_counts":   bucket_counts,
        "bucket_accuracy_exact":      exact,
        "bucket_accuracy_within_one": within_one,
        "spearman":        spearman,
        "kendall_tau":     kendall,
        "confusion":       conf,
        "confusion_axes":  BUCKETS,
        "per_pair":        per_pair,
        "dropped_no_metrics":     len(dropped_no_metrics),
        "dropped_unknown_bucket": len(dropped_unknown_bucket),
        "trained_at":      time.time(),
    }


# ── File-driven orchestration ───────────────────────────────────────────────

PROJECT_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RANKING_FILE  = os.path.join(PROJECT_ROOT, "web", "data", "ranking.json")
OUT_FILE      = os.path.join(PROJECT_ROOT, "web", "data", "fitness.json")


def _load_metrics_from_snapshot(snapshot: list) -> Dict[str, List[float]]:
    """Pull metric vectors from a snapshot list. Skips entries with empty components."""
    out: Dict[str, List[float]] = {}
    for entry in snapshot or []:
        pid = entry.get("pair_id")
        if not pid:
            continue
        vec = _vec_from_components(entry.get("components") or {})
        if vec is None:
            continue
        out[pid] = vec
    return out


def train_from_files() -> Dict[str, Any]:
    """
    Load buckets and metric vectors from ranking.json (the single source of
    truth for human bucket labels). Returns the same dict shape as
    train_from_dicts.
    """
    if not os.path.exists(RANKING_FILE):
        return {"ok": False,
                "error": f"No ranking.json at {RANKING_FILE}. Bucket some pairs first."}
    with open(RANKING_FILE, "r") as f:
        ranking = json.load(f)
    buckets = ranking.get("buckets") or {}
    if not buckets:
        return {"ok": False,
                "error": "ranking.json has no bucket labels."}
    metrics = _load_metrics_from_snapshot(ranking.get("snapshot") or [])
    if not metrics:
        return {"ok": False,
                "error": ("No metric vectors in ranking.json. Click "
                          "Train fitness in the Human Ranking tab — it "
                          "POSTs the pair-lab vectors from localStorage "
                          "alongside the bucket labels.")}
    return train_from_dicts(buckets, metrics)


def train_and_save() -> Dict[str, Any]:
    """Convenience: train from files, atomically write the result to disk."""
    result = train_from_files()
    if not result.get("ok"):
        return result
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    tmp = OUT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(result, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUT_FILE)
    result["output_path"] = OUT_FILE
    return result


if __name__ == "__main__":
    out = train_and_save()
    if not out.get("ok"):
        print("FAILED:", out.get("error"))
        raise SystemExit(1)
    print(f"Trained on {out['n_pairs']} pairs.")
    print(f"  Bucket accuracy (exact)    : {out['bucket_accuracy_exact']:.3f}")
    print(f"  Bucket accuracy (±1)       : {out['bucket_accuracy_within_one']:.3f}")
    print(f"  Spearman    rank correlation: {out['spearman']:.3f}")
    print(f"  Kendall tau rank correlation: {out['kendall_tau']:.3f}")
    print()
    print("Standardised weights (per-feature relative importance):")
    for f in FEATURE_NAMES:
        w = out['weights_standardised'][f]
        bar = "+" * int(round(abs(w) * 10))
        sign = " " if w >= 0 else "-"
        print(f"  {f:20s}: {sign}{abs(w):.3f}  {bar}")
    print()
    print(f"Saved to {out['output_path']}")
