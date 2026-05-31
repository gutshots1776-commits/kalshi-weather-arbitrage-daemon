#!/usr/bin/env python3
"""
Forecast-calibration & edge-realization report for the Kalshi weather daemon.

Reads the settlement log (``kalshi_settlement_log.jsonl``) — which pairs each
position's model-predicted win probability with its realized win/loss — and
answers the question a trading model actually has to defend:

    *Is the model calibrated, and does its predicted edge show up in realized P&L?*

Metrics:
  - Brier score and Brier skill score (vs. a climatology baseline)
  - Log loss
  - A binned reliability table + ASCII calibration curve (predicted vs. empirical)
  - Expected Calibration Error (ECE) and Maximum Calibration Error (MCE)
  - Predicted vs. realized per-contract edge (does the edge materialize?)
  - Win rate and P&L by confidence bucket
  - Overall win rate, P&L, and ROI

Pure standard library. An optional PNG reliability diagram is written with
``--plot`` if matplotlib is installed.

Usage:
    python analyze_calibration.py [--log PATH] [--bins N] [--mode all|paper|live] [--plot OUT.png]
"""
import argparse
import json
import math
import os
from collections import defaultdict


DEFAULT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "kalshi_settlement_log.jsonl")


# ── Loading ──────────────────────────────────────────────────────────────

def _predicted_win_prob(entry):
    """Best available model P(position wins), in [0, 1], or None.

    Prefers the explicit ``predicted_fair_cents`` column; falls back to the
    legacy ``fair_cents``; finally reconstructs from ``raw_edge + price_cents``
    (approximate — ignores the half-spread haircut).
    """
    for key in ("predicted_fair_cents", "fair_cents"):
        v = entry.get(key)
        if isinstance(v, (int, float)):
            return min(1.0, max(0.0, v / 100.0))
    raw_edge, price = entry.get("raw_edge"), entry.get("price_cents")
    if isinstance(raw_edge, (int, float)) and isinstance(price, (int, float)):
        return min(1.0, max(0.0, (raw_edge + price) / 100.0))
    return None


def load_settlements(path, mode="all"):
    """Return a list of normalized settled-position records usable for scoring."""
    if not os.path.exists(path):
        return []

    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "won" not in e:
                continue
            is_paper = bool(e.get("paper_trade"))
            if mode == "paper" and not is_paper:
                continue
            if mode == "live" and is_paper:
                continue
            p = _predicted_win_prob(e)
            if p is None:
                continue
            rows.append({
                "p": p,
                "y": 1 if e.get("won") else 0,
                "price": e.get("price_cents") or 0,
                "pnl": e.get("pnl_cents") or 0,
                "cost": e.get("cost_cents") or 0,
                "confidence": e.get("confidence"),
                "paper": is_paper,
            })
    return rows


# ── Scoring ──────────────────────────────────────────────────────────────

def brier_score(rows):
    return sum((r["p"] - r["y"]) ** 2 for r in rows) / len(rows)


def log_loss(rows, eps=1e-15):
    total = 0.0
    for r in rows:
        p = min(1 - eps, max(eps, r["p"]))
        total += -(r["y"] * math.log(p) + (1 - r["y"]) * math.log(1 - p))
    return total / len(rows)


def reliability_table(rows, bins):
    """Return per-bin (lo, hi, n, mean_pred, emp_rate) plus ECE and MCE."""
    buckets = defaultdict(list)
    for r in rows:
        idx = min(bins - 1, int(r["p"] * bins))
        buckets[idx].append(r)

    table, ece, mce, n = [], 0.0, 0.0, len(rows)
    for idx in range(bins):
        group = buckets.get(idx, [])
        if not group:
            continue
        cnt = len(group)
        mean_pred = sum(g["p"] for g in group) / cnt
        emp = sum(g["y"] for g in group) / cnt
        gap = abs(mean_pred - emp)
        ece += (cnt / n) * gap
        mce = max(mce, gap)
        table.append((idx / bins, (idx + 1) / bins, cnt, mean_pred, emp))
    return table, ece, mce


def by_confidence(rows):
    """Win rate and mean P&L grouped into 0.1-wide confidence buckets."""
    buckets = defaultdict(list)
    for r in rows:
        c = r["confidence"]
        if not isinstance(c, (int, float)):
            continue
        buckets[min(9, int(c * 10))].append(r)
    out = []
    for idx in sorted(buckets):
        group = buckets[idx]
        out.append((idx / 10, (idx + 1) / 10, len(group),
                    sum(g["y"] for g in group) / len(group),
                    sum(g["pnl"] for g in group) / len(group)))
    return out


# ── Rendering ────────────────────────────────────────────────────────────

def _curve_row(mean_pred, emp, width=24):
    """One ASCII calibration-curve row: 'P' = predicted, 'E' = empirical."""
    cells = [" "] * (width + 1)
    pp = min(width, max(0, round(mean_pred * width)))
    pe = min(width, max(0, round(emp * width)))
    cells[pp] = "P"
    cells[pe] = "*" if pe == pp else "E"
    return "".join(cells)


def render_report(rows, bins, mode, path):
    n = len(rows)
    lines = []
    add = lines.append

    add("=" * 72)
    add(f"  Calibration & Edge Report — {os.path.basename(path)}  (mode={mode})")
    add("=" * 72)

    if n == 0:
        add("")
        add("No scored settlements found.")
        add("This is expected on a fresh checkout: the report needs settled")
        add("positions with a predicted probability. Run the daemon in paper")
        add("mode for a while (python kalshi_unified.py) to generate")
        add("kalshi_settlement_log.jsonl, then re-run this script.")
        add("=" * 72)
        return "\n".join(lines)

    wins = sum(r["y"] for r in rows)
    ybar = wins / n
    total_pnl = sum(r["pnl"] for r in rows)
    total_cost = sum(r["cost"] for r in rows)
    roi = (total_pnl / total_cost) if total_cost else float("nan")

    brier = brier_score(rows)
    base = ybar * (1 - ybar)                      # climatology Brier
    bss = (1 - brier / base) if base > 0 else float("nan")
    ll = log_loss(rows)

    pred_edge = sum(r["p"] * 100 - r["price"] for r in rows) / n
    real_edge = sum(100 * r["y"] - r["price"] for r in rows) / n

    add("")
    add(f"  Settled positions : {n}")
    add(f"  Wins / losses     : {wins} / {n - wins}   (win rate {ybar:.1%})")
    add(f"  Total P&L         : {total_pnl:+d}c  (${total_pnl/100:+.2f})")
    add(f"  Total cost        : {total_cost}c     ROI {roi:+.1%}")
    add("")
    add("  ── Probabilistic accuracy ─────────────────────────────────────")
    add(f"  Brier score       : {brier:.4f}   (lower is better; 0.25 = coin flip)")
    add(f"  Brier skill score : {bss:+.3f}    (>0 beats predicting the base rate)")
    add(f"  Log loss          : {ll:.4f}")
    add("")
    add("  ── Edge realization (per contract, cents) ─────────────────────")
    add(f"  Mean predicted edge : {pred_edge:+.1f}c")
    add(f"  Mean realized edge  : {real_edge:+.1f}c")
    if abs(pred_edge) > 1e-9:
        add(f"  Realization ratio   : {real_edge / pred_edge:+.2f}  "
            f"(1.0 = edge fully materialized)")

    # Reliability table + ASCII curve
    table, ece, mce = reliability_table(rows, bins)
    add("")
    add("  ── Reliability (calibration) ──────────────────────────────────")
    add(f"  ECE {ece:.3f}   MCE {mce:.3f}      P=mean predicted   E=empirical")
    add("  bin          n   pred    emp    0" + " " * 19 + "1")
    for lo, hi, cnt, mean_pred, emp in table:
        add(f"  {lo:.2f}-{hi:.2f} {cnt:4d}  {mean_pred:.3f}  {emp:.3f}  |{_curve_row(mean_pred, emp)}|")

    conf = by_confidence(rows)
    if conf:
        add("")
        add("  ── By confidence bucket ───────────────────────────────────────")
        add("  confidence     n   win%    avg P&L")
        for lo, hi, cnt, wr, avg_pnl in conf:
            add(f"  {lo:.1f}-{hi:.1f}     {cnt:4d}  {wr:5.1%}   {avg_pnl:+6.1f}c")

    add("=" * 72)
    return "\n".join(lines)


def save_plot(rows, bins, out_path):
    """Write a PNG reliability diagram. Returns True on success."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False

    table, _, _ = reliability_table(rows, bins)
    xs = [mp for _, _, _, mp, _ in table]
    ys = [emp for _, _, _, _, emp in table]
    sizes = [max(20, cnt * 8) for _, _, cnt, _, _ in table]

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], "--", color="gray", label="perfect calibration")
    ax.scatter(xs, ys, s=sizes, alpha=0.7, label="model")
    ax.set_xlabel("Predicted win probability")
    ax.set_ylabel("Empirical win rate")
    ax.set_title("Reliability diagram")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    return True


# ── CLI ──────────────────────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="Calibration & edge report for the Kalshi weather daemon.")
    ap.add_argument("--log", default=DEFAULT_LOG, help="settlement JSONL log path")
    ap.add_argument("--bins", type=int, default=10, help="number of reliability bins")
    ap.add_argument("--mode", choices=["all", "paper", "live"], default="all",
                    help="filter by trade mode")
    ap.add_argument("--plot", metavar="OUT.png", help="also write a PNG reliability diagram")
    args = ap.parse_args(argv)

    rows = load_settlements(args.log, mode=args.mode)
    print(render_report(rows, args.bins, args.mode, args.log))

    if args.plot and rows:
        if save_plot(rows, args.bins, args.plot):
            print(f"\nReliability diagram written to {args.plot}")
        else:
            print("\n(matplotlib not installed — skipped PNG; install it for --plot)")


if __name__ == "__main__":
    main()
