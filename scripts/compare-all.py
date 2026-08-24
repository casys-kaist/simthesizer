from pathlib import Path

import typer
from simthesizer.harness import experiment_name, iter_experiments, result_jsonl_path
from simthesizer.throughput import (
    ThroughputError,
    align_series,
    compute_throughput_series,
    constrained_dtw_distance,
)


def compare_pair(
    reference_jsonl: Path,
    candidate_jsonl: Path,
    window_sec: float,
    warp_sec: float,
) -> str:
    if not reference_jsonl.is_file() or not candidate_jsonl.is_file():
        return "-"

    reference_times, reference_values = compute_throughput_series(
        reference_jsonl, window_sec
    )
    candidate_times, candidate_values = compute_throughput_series(
        candidate_jsonl, window_sec
    )
    _, reference_values, candidate_values = align_series(
        reference_times, reference_values, candidate_times, candidate_values
    )
    distance = constrained_dtw_distance(
        reference_values,
        candidate_values,
        round(warp_sec / window_sec),
    )
    return f"{distance * 100.0:.6f}%"


def print_table(rows: list[tuple[str, str, str]]) -> None:
    headers = ("experiment", "simthesizer_vs_real", "baseline_vs_real")
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(3)
    ]
    header = "  ".join(
        title.ljust(widths[index]) for index, title in enumerate(headers)
    )
    divider = "  ".join("-" * widths[index] for index in range(3))
    typer.echo(header)
    typer.echo(divider)
    for row in rows:
        typer.echo(
            "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        )


def main(
    window_sec: float = typer.Option(1.0, "--window-sec"),
    warp_sec: float = typer.Option(3.0, "--warp-sec"),
) -> None:
    if warp_sec < 0.0:
        raise SystemExit("warp window must be non-negative")

    rows = []
    had_error = False
    for experiment in iter_experiments():
        try:
            simthesizer_vs_real = compare_pair(
                result_jsonl_path(experiment, "real"),
                result_jsonl_path(experiment, "simthesizer"),
                window_sec,
                warp_sec,
            )
            baseline_vs_real = compare_pair(
                result_jsonl_path(experiment, "real"),
                result_jsonl_path(experiment, "baseline"),
                window_sec,
                warp_sec,
            )
        except ThroughputError as exc:
            typer.echo(f"failed: {experiment_name(experiment)}: {exc}", err=True)
            simthesizer_vs_real = "ERR"
            baseline_vs_real = "ERR"
            had_error = True

        rows.append((experiment_name(experiment), simthesizer_vs_real, baseline_vs_real))

    print_table(rows)
    if had_error:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
