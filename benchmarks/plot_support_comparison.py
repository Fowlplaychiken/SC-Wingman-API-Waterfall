"""Plot the Wingman Support-only 24-case-per-condition comparison; never calls a provider."""

from __future__ import annotations

import argparse
import json
import textwrap
from itertools import cycle
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402


def _save(fig, output: Path) -> None:
    for suffix in ("png", "svg"):
        fig.savefig(output.with_suffix("." + suffix), dpi=180, facecolor=fig.get_facecolor())
    svg = output.with_suffix(".svg")
    svg.write_text(
        "\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n",
        encoding="utf-8",
    )
    plt.close(fig)


def render(source: Path, output: Path) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    rows = data["rows"]
    total = data["cases_per_model"]
    fig, axes = plt.subplots(1, 3, figsize=(17, 8), sharey=True)
    fig.patch.set_facecolor("#0c1424")
    fig.subplots_adjust(left=0.34, right=0.97, top=0.70, bottom=0.29, wspace=0.32)
    labels = [row["label"].replace("\n", " ") for row in rows]
    titles = (
        "Time · lower is better",
        "Confirmed correct · higher is better",
        "Estimated API cost · lower is better",
    )
    for ax, title in zip(axes, titles):  # noqa: B905
        ax.set_facecolor("#111d30")
        ax.spines[:].set_visible(False)
        ax.tick_params(colors="#dbe6f5", length=0, labelsize=9)
        ax.xaxis.grid(True, color="#33445a", alpha=0.5)
        ax.set_axisbelow(True)
        ax.set_title(title, loc="left", color="white", fontsize=11, pad=18)
    axes[0].set_yticks(range(len(rows)), labels=labels)
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, max(row["median_seconds"] for row in rows) * 1.65)
    axes[0].set_xlabel("All-attempt median · seconds", color="#b8c9df", labelpad=10)
    axes[1].set_xlim(0, 100)
    axes[1].set_xticks([0, 25, 50, 75, 100])
    axes[1].set_xlabel(f"Fully correct / all {total} cases · percent", color="#b8c9df", labelpad=10)
    known_costs = [row["usd_per_1000"] for row in rows if row["usd_per_1000"] is not None]
    cost_limit = max(known_costs, default=0.1) * 1.8
    axes[2].set_xlim(0, cost_limit)
    axes[2].set_xlabel("USD per 1,000 tasks · estimate where known", color="#b8c9df", labelpad=10)
    palette = cycle(("#63c4e1", "#ffbb70", "#b99aff", "#7ed7a2", "#ff7e8b", "#67d7d1"))
    colors = [next(palette) for _ in rows]
    for y, (row, color) in enumerate(zip(rows, colors)):  # noqa: B905
        vals = (row["median_seconds"], 100 * row["correct"] / total, row["usd_per_1000"])
        for ax, value in zip(axes[:2], vals[:2]):  # noqa: B905
            ax.scatter(value, y, s=58, color=color, zorder=3)
        axes[0].text(vals[0] + 0.035, y, f"{vals[0]:.2f} s", va="center", color=color, fontsize=8.5)
        axes[1].text(
            vals[1] + 1.5,
            y,
            f"{vals[1]:.1f}% · {row['correct']}/{total}",
            va="center",
            color=color,
            fontsize=8.5,
        )
        if vals[2] is None:
            axes[2].text(
                0.015 * cost_limit,
                y,
                "Plan allowance / cost unknown",
                va="center",
                color=color,
                fontsize=8.5,
            )
        else:
            axes[2].scatter(vals[2], y, s=58, color=color, zorder=3)
            axes[2].text(
                vals[2] + max(vals[2] * 0.04, cost_limit * 0.008),
                y,
                f"${vals[2]:.3f}" if vals[2] else "$0",
                va="center",
                color=color,
                fontsize=8.5,
            )
    fig.text(
        0.03,
        0.93,
        "Wingman · Support-only model comparison",
        color="white",
        fontsize=23,
        weight="bold",
    )
    fig.text(
        0.03,
        0.87,
        "24 tasks per condition · 9 November 2026 · blind output review",
        color="#b8c9df",
        fontsize=12,
    )
    for ax in axes:
        ax.set_ylim(-0.6, len(rows) - 0.4)
    axes[0].invert_yaxis()
    handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            color=c,
            linestyle="",
            markersize=8,
            label=row["label"].replace("\n", " "),
        )
        for row, c in zip(rows, colors)  # noqa: B905
    ]
    legend = fig.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(0.03, 0.83),
        frameon=False,
        ncol=2,
        fontsize=8.5,
    )
    for label in legend.get_texts():
        label.set_color("#dbe6f5")
    footer_source = " ".join(
        (
            "Per condition: 8 summaries, 8 memory extractions, "
            "8 tool compressions. Partial is not correct.",
            "Two blind AI review batches; ten seeded passing items per batch were checked "
            "by the lead "
            "(20/20 agreement). "
            "Development fixtures, not human gold or held-out accuracy.",
            "Hosted cost is unknown, not zero. Direct Haiku's uncached estimate is not an invoice; "
            "local Qwen $0 excludes hardware/electricity.",
            "Direct Haiku used adaptive thinking; stock hosted Support had reasoning off. "
            "Conditions differ, so do not attribute results to hosting alone.",
            data["hardware"],
        )
    )
    footer = textwrap.fill(footer_source, width=170)
    fig.text(0.03, 0.19, footer, color="#b8c9df", fontsize=8, va="top", wrap=True, linespacing=1.35)
    _save(fig, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Output path without extension")
    args = parser.parse_args()
    render(args.input, args.output)
