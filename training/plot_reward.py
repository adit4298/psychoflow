"""§16 checkpoint reporting: literal reward-curve table + PNG from monitor.csv.

Reads SB3 Monitor CSV(s) (one row per completed episode: r=episode total
reward, l=episode length in decision steps, t=wall time). Converts to MEAN
REWARD PER STEP (r/l), not raw episode total — every baseline number already
recorded in this project (CLAUDE.md's Checkpoint-1 table, run_tier0_episode.py)
is mean reward/step, so plotting raw episode totals would not be comparable.

Accepts multiple monitor files in chronological order (e.g. Burst A's
monitor.csv followed by Burst B's monitor_burstB.csv) and concatenates them
with each later file's timestep column offset by the running total, so a
multi-burst run plots as one continuous curve rather than two 0-based
segments.

Usage:
    python -m training.plot_reward training/checkpoints/stage1/monitor.csv \\
        --out training/checkpoints/stage1/reward_curve.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def load_monitor(paths: list[Path]) -> pd.DataFrame:
    """Concatenate monitor CSVs, offsetting cumulative timesteps across files."""
    frames = []
    step_offset = 0
    for path in paths:
        # SB3 Monitor's first line is a JSON header comment; pandas skips it
        # via skiprows=1.
        df = pd.read_csv(path, skiprows=1)
        df["episode_timesteps"] = df["l"].cumsum() + step_offset
        df["mean_reward_per_step"] = df["r"] / df["l"]
        frames.append(df)
        step_offset = df["episode_timesteps"].iloc[-1]
    return pd.concat(frames, ignore_index=True)


def report(paths: list[Path], out_png: Path) -> pd.DataFrame:
    df = load_monitor(paths)

    # Rolling mean (window=5, min_periods=1) is printed ALONGSIDE the raw
    # per-episode column, never in place of it — the raw column is what
    # actually happened and is not monotonic; only the smoothed column is
    # expected to trend cleanly. A checkpoint report that shows only the
    # rolling mean lets a smoothed claim stand in for what the raw data
    # shows, which is exactly the mistake to avoid here.
    df["rolling_mean_5"] = df["mean_reward_per_step"].rolling(window=min(5, len(df)), min_periods=1).mean()

    print(f"{'episode':>8} {'timesteps':>10} {'mean_reward_per_step':>22} {'rolling_mean_5':>16}")
    for i, row in df.iterrows():
        print(f"{i + 1:>8} {int(row['episode_timesteps']):>10} {row['mean_reward_per_step']:>22.4f} "
              f"{row['rolling_mean_5']:>16.4f}")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(df["episode_timesteps"], df["mean_reward_per_step"], marker="o", label="per-episode (raw)")
    ax.plot(df["episode_timesteps"], df["rolling_mean_5"], linewidth=2, label="rolling mean (window=5)")
    ax.set_xlabel("timesteps (decision steps)")
    ax.set_ylabel("mean reward / step")
    ax.set_title("§16 checkpoint: reward curve")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    print(f"\nSaved {out_png}")

    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("monitor_csvs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report(args.monitor_csvs, args.out)


if __name__ == "__main__":
    main()
