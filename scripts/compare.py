from pathlib import Path

import typer
from simthesizer.throughput import (
    ThroughputError,
    align_series,
    compute_throughput_series,
    constrained_dtw_distance,
)


def main(
    reference_jsonl: Path = typer.Argument(),
    candidate_jsonl: Path = typer.Argument(),
    window_sec: float = typer.Option(1.0, "--window-sec"),
    warp_sec: float = typer.Option(
        3.0,
        "--warp-sec",
        help="Maximum time shift DTW may align across when matching throughput curves.",
    ),
) -> None:
    try:
        if warp_sec < 0.0:
            raise ThroughputError("warp window must be non-negative")

        reference_times, reference_values = compute_throughput_series(
            reference_jsonl, window_sec
        )
        candidate_times, candidate_values = compute_throughput_series(
            candidate_jsonl, window_sec
        )
        _, reference_values, candidate_values = align_series(
            reference_times, reference_values, candidate_times, candidate_values
        )
    except ThroughputError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    distance = constrained_dtw_distance(
        reference_values,
        candidate_values,
        round(warp_sec / window_sec),
    )
    typer.echo(f"{distance * 100.0:.6f}%")


if __name__ == "__main__":
    typer.run(main)
