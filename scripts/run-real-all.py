import subprocess
import sys

import typer
from simthesizer.harness import (
    REPO_ROOT,
    SCRIPT_ROOT,
    experiment_name,
    iter_experiments,
    result_jsonl_path,
    runner_config_path,
    workload_path,
)


def main(
    full: bool = typer.Option(False, "--full"),
) -> None:
    succeeded = 0
    failed = 0
    skipped = 0
    pending = []
    for experiment in iter_experiments():
        config_path = runner_config_path(experiment, "real")
        if config_path is None:
            typer.echo(f"skip: {experiment_name(experiment)} (no real config)")
            skipped += 1
            continue
        output_jsonl = result_jsonl_path(experiment, "real")
        if not full and output_jsonl.is_file():
            typer.echo(f"skip: {experiment_name(experiment)} ({output_jsonl} exists)")
            skipped += 1
            continue
        pending.append((experiment, config_path))

    for experiment, config_path in pending:
        output_jsonl = result_jsonl_path(experiment, "real")
        output_jsonl.parent.mkdir(parents=True, exist_ok=True)

        typer.echo(f"[real] {experiment_name(experiment)}")
        command = [
            sys.executable,
            str(SCRIPT_ROOT / "run-real.py"),
            "--config",
            str(config_path),
            "--workload",
            str(workload_path(experiment)),
            "--output-jsonl",
            str(output_jsonl),
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
