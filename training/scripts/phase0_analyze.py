"""Phase 0 analysis — reads phase0_emergency.json, computes the four asks.

Pure post-processing. Reads one JSON file; writes nothing to the repo.
No repo module imported at all.
"""
from __future__ import annotations

import json
import math
import statistics as st
from pathlib import Path

J = Path(r"C:\Users\aditp\OneDrive\Documents\GitHub\Test"
         r"\training\checkpoints\_sweeps\phase0_emergency.json")


def spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):          # average ties
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    mx, my = st.fmean(rx), st.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def linfit(x, y):
    """slope, intercept via least squares."""
    n = len(x)
    mx, my = st.fmean(x), st.fmean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    slope = sxy / sxx if sxx else float("nan")
    return slope, my - slope * mx


def main():
    d = json.loads(J.read_text())
    p2 = d["part2"]
    steps = sorted(int(k) for k in p2)

    print("=" * 78)
    print("(1)+(2) PROPOSAL QUALITY PER CHECKPOINT  —  mean & std across 3 seeds")
    print("=" * 78)
    print(f"{'step':>8} {'mean_q':>8} {'std_q':>7} {'min':>6} {'max':>6} "
          f"{'range':>7} | {'per-seed (1/7/42)':>26} | {'pooled':>7} {'dec':>5}")
    means, stds, pooled_l = [], [], []
    for s in steps:
        rows = p2[str(s)]
        qs = [r["proposal_quality"] for r in rows if r["proposal_quality"] is not None]
        served = sum(r["served"] for r in rows)
        avoid = sum(r["blocked_avoidable"] for r in rows)
        pooled = served / (served + avoid) if (served + avoid) else float("nan")
        if not qs:
            print(f"{s:>8}   (no decidable ambulance junction-steps)")
            continue
        m = st.fmean(qs)
        sd = st.stdev(qs) if len(qs) > 1 else 0.0
        means.append(m); stds.append(sd); pooled_l.append(pooled)
        per = "/".join(f"{q:.3f}" for q in qs)
        print(f"{s:>8} {m:>8.3f} {sd:>7.3f} {min(qs):>6.3f} {max(qs):>6.3f} "
              f"{max(qs)-min(qs):>7.3f} | {per:>26} | {pooled:>7.3f} "
              f"{served+avoid:>5}")

    print(f"\n  mean of per-checkpoint STD across seeds : {st.fmean(stds):.4f}")
    print(f"  mean of per-checkpoint RANGE            : "
          f"{st.fmean([max([r['proposal_quality'] for r in p2[str(s)] if r['proposal_quality'] is not None]) - min([r['proposal_quality'] for r in p2[str(s)] if r['proposal_quality'] is not None]) for s in steps]):.4f}")
    print(f"  STD of the 12 checkpoint MEANS          : "
          f"{st.stdev(means) if len(means)>1 else 0:.4f}")
    print("  -> if within-checkpoint std >= between-checkpoint std, seed/scenario")
    print("     variance dominates and no trend is readable at n=3.")

    print()
    print("=" * 78)
    print("(3) TREND STATISTICS across the 12 checkpoint means")
    print("=" * 78)
    xs = [float(s) for s in steps][:len(means)]
    slope, icpt = linfit(xs, means)
    rho = spearman(xs, means)
    # Pearson for reference
    mx, my = st.fmean(xs), st.fmean(means)
    num = sum((a-mx)*(b-my) for a, b in zip(xs, means))
    den = math.sqrt(sum((a-mx)**2 for a in xs)*sum((b-my)**2 for b in means))
    pear = num/den if den else float('nan')
    print(f"  linear slope        : {slope:+.3e} quality per timestep")
    print(f"                        = {slope*100000:+.4f} per 100k steps")
    print(f"  Spearman rho        : {rho:+.4f}")
    print(f"  Pearson r           : {pear:+.4f}  (r2={100*pear*pear:.2f}%)")
    print(f"  first ckpt mean     : {means[0]:.3f}   last ckpt mean: {means[-1]:.3f}")
    print(f"  early half mean     : {st.fmean(means[:len(means)//2]):.3f}")
    print(f"  late  half mean     : {st.fmean(means[len(means)//2:]):.3f}")

    print()
    print("=" * 78)
    print("(4) AMBULANCE-VISIBLE STEPS — all 36 Part-2 episodes")
    print("=" * 78)
    allrows = [r for s in steps for r in p2[str(s)]]
    for label, key in [("amb_visible_steps", "amb_visible_steps"),
                       ("amb_visible_pct", "amb_visible_pct"),
                       ("amb_junction_steps", "amb_junction_steps"),
                       ("episode steps", "steps")]:
        v = [r[key] for r in allrows]
        print(f"  {label:<20} n={len(v):>3} mean={st.fmean(v):>7.2f} "
              f"median={st.median(v):>6.1f} min={min(v):>5.1f} max={max(v):>6.1f} "
              f"stdev={st.stdev(v):>6.2f}")
    if d.get("part1"):
        print("\n  PART 1 (12 distinct seeds, latest ckpt):")
        for key in ["amb_visible_steps", "amb_junction_steps"]:
            v = [r[key] for r in d["part1"]]
            print(f"    {key:<20} n={len(v)} mean={st.fmean(v):.2f} "
                  f"min={min(v)} max={max(v)} stdev={st.stdev(v):.2f}")
    fc = [r["first_contact_served"] for r in allrows if r["first_contact_served"] is not None]
    if fc:
        print(f"\n  first_contact_served: {sum(fc)}/{len(fc)} = {100*sum(fc)/len(fc):.1f}%")
    un = sum(r["blocked_unavoidable"] for r in allrows)
    tot = sum(r["amb_junction_steps"] for r in allrows)
    print(f"  blocked_unavoidable (mask-locked, excluded): {un}/{tot} "
          f"= {100*un/max(1,tot):.1f}% of ambulance junction-steps")


if __name__ == "__main__":
    main()
