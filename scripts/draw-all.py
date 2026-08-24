import subprocess
import sys

import typer
from simthesizer.harness import (
    REPO_ROOT,
    SCRIPT_ROOT,
    experiment_name,
    experiment_output_dir,
    iter_experiments,
    result_jsonl_path,
    throughput_png_path,
)


def main(
    window_sec: float = typer.Option(1.0, "--window-sec"),
) -> None:
    succeeded = 0
    skipped = 0
    failed = 0
    for experiment in iter_experiments():
        baseline_exists = result_jsonl_path(experiment, "baseline").is_file()
        simthesizer_exists = result_jsonl_path(experiment, "simthesizer").is_file()
        real_exists = result_jsonl_path(experiment, "real").is_file()
        if not (baseline_exists or simthesizer_exists or real_exists):
            typer.echo(
                f"skip: no JSONLs found under {experiment_output_dir(experiment)}"
            )
            skipped += 1
            continue

        typer.echo(f"[draw] {experiment_name(experiment)}")
        command = [
            sys.executable,
            str(SCRIPT_ROOT / "draw.py"),
            str(experiment_output_dir(experiment)),
            "--output",
            str(throughput_png_path(experiment)),
            "--window-sec",
            str(window_sec),
        ]
        result = subprocess.run(command, cwd=REPO_ROOT)
        if result.returncode == 0:
            succeeded += 1
        else:
            failed += 1
            typer.echo(
                f"failed: {experiment_name(experiment)} (exit={result.returncode})",
                err=True,
            )

    typer.echo(f"summary: succeeded={succeeded} skipped={skipped} failed={failed}")
    if failed:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
