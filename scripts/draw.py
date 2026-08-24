from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import typer
from simthesizer.throughput import ThroughputError, align_series, compute_throughput_series
from matplotlib import pyplot as plt
from matplotlib.ticker import FuncFormatter


def y_tick_formatter(value: float, _position: float) -> str:
    if abs(value) >= 1000.0:
        return f"{value / 1000.0:.0f}K"
    return f"{value:.0f}"


def discover_series_specs(output_dir: Path) -> list[tuple[str, Path, str, float]]:
    return [
        (label, path, color, linewidth)
        for label, path, color, linewidth in [
            ("baseline", output_dir / "baseline.jsonl", "#b3b3b3", 3.5),
            ("simthesizer", output_dir / "simthesizer.jsonl", "#2f9e44", 2.8),
            ("real", output_dir / "real.jsonl", "#d9480f", 2.8),
        ]
        if path.is_file()
    ]


def align_values_to_axis(
    series_times: list[float],
    series_values: list[float],
    times: list[float],
) -> list[float]:
    if not series_times:
        return [0.0 for _ in times]
    _, aligned_values, _ = align_series(
        series_times,
        series_values,
        times,
        [0.0 for _ in times],
    )
    return aligned_values


def draw_plot(
    output_path: Path,
    series_specs: list[tuple[str, Path, str, float]],
    window_sec: float,
    title: str | None,
) -> None:
    series_data = [
        (label, *compute_throughput_series(path, window_sec), color, linewidth)
        for label, path, color, linewidth in series_specs
    ]
    max_len = max(
        (len(series_times) for _, series_times, _, _, _ in series_data), default=0
    )
    times = [index * window_sec for index in range(max(max_len, 1))]

    fig, ax = plt.subplots(figsize=(8.0, 4.8), constrained_layout=True)
    fig.patch.set_facecolor("#dddddd")
    ax.set_facecolor("#dddddd")
    ax.grid(True, color="#bfbfbf", linewidth=1.0)
    ax.set_axisbelow(True)

    for label, series_times, series_values, color, linewidth in series_data:
        ax.plot(
            times,
            align_values_to_axis(series_times, series_values, times),
            color=color,
            linewidth=linewidth,
            label=label,
        )

    for spine in ax.spines.values():
        spine.set_linewidth(1.8)

    ax.tick_params(axis="both", labelsize=14, width=1.6, length=6, pad=10)
    ax.yaxis.set_major_formatter(FuncFormatter(y_tick_formatter))
    ax.set_xlabel("Time (sec)", fontsize=20)
    ax.set_ylabel("Throughput (tok/s)", fontsize=20)
    if title:
        ax.set_title(title, fontsize=18)
    ax.legend(frameon=False, fontsize=13, loc="upper right")
    ax.set_xlim(left=0.0)
    ax.set_ylim(bottom=0.0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def main(
    output_dir: Path = typer.Argument(),
    output: Path = typer.Option(..., "--output"),
    window_sec: float = typer.Option(1.0, "--window-sec"),
    title: str | None = typer.Option(None, "--title"),
) -> None:
    series_specs = discover_series_specs(output_dir)
    if not series_specs:
        typer.echo(f"skip: no JSONLs found under {output_dir}")
        return

    try:
        draw_plot(output, series_specs, window_sec, title or output_dir.name)
    except ThroughputError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(str(output))


if __name__ == "__main__":
    typer.run(main)
