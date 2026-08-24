import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import typer
from simthesizer.harness import (
    REPO_ROOT,
    Experiment,
    experiment_name,
    format_override_summary,
    iter_experiments,
    resolve_host_override,
    result_jsonl_path,
    runner_config_path,
    workload_path,
)


def workspace_path(workspace_root: Path, repo_path: Path) -> Path:
    return workspace_root / repo_path.relative_to(REPO_ROOT)


def replace_device_mem_assignments(config_text: str, value: int) -> tuple[str, int]:
    lines = []
    replaced = 0
    pattern = re.compile(r"^(?P<indent>\s*)device_mem\s*=")
    for line in config_text.splitlines(keepends=True):
        match = pattern.match(line)
        if match:
            indent = match.group("indent")
            newline = "\n" if line.endswith("\n") else ""
            lines.append(f"{indent}device_mem = {value}{newline}")
            replaced += 1
            continue
        lines.append(line)
    return "".join(lines), replaced


def prepare_workspace_config(
    workspace_root: Path,
    config_path: Path,
    override: dict[str, object],
) -> Path:
    workspace_config_path = workspace_path(workspace_root, config_path)
    config_text = workspace_config_path.read_text(encoding="utf-8")

    device_mem_bytes = override.get("device_mem_bytes")
    if device_mem_bytes is not None:
        if not isinstance(device_mem_bytes, int):
            raise SystemExit(
                f"simthesizer device_mem_bytes must be an integer: {device_mem_bytes}"
            )
        config_text, replaced = replace_device_mem_assignments(
            config_text,
            device_mem_bytes,
        )
        if replaced == 0:
            raise SystemExit(
                f"failed to find any device_mem entries in simthesizer config: {config_path}"
            )

    workspace_config_path.write_text(config_text, encoding="utf-8")
    return workspace_config_path.resolve()


def create_workspace(
    workspace_root: Path,
    experiments: list[tuple[Experiment, Path]],
) -> None:
    shutil.copytree(REPO_ROOT / "data", workspace_root / "data", dirs_exist_ok=True)

    for _, config_path in experiments:
        workspace_config_path = workspace_path(workspace_root, config_path)
        workspace_config_path.parent.mkdir(parents=True, exist_ok=True)
        workspace_config_path.write_text(
            config_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def main(
    host: str | None = typer.Option(None, "--host"),
) -> None:
    selected_host, override = resolve_host_override(host)
    typer.echo(f"host={selected_host} override={format_override_summary(override)}")
    typer.echo("[simthesizer] cargo build --release")
    build_result = subprocess.run(
        ["cargo", "build", "--release", "--quiet"],
        cwd=REPO_ROOT,
    )
    if build_result.returncode != 0:
        raise typer.Exit(code=build_result.returncode)

    succeeded = 0
    failed = 0
    pending = []
    skipped = 0
    for experiment in iter_experiments():
        config_path = runner_config_path(experiment, "simthesizer")
        if config_path is None:
            typer.echo(f"skip: {experiment_name(experiment)} (no simthesizer config)")
            skipped += 1
            continue
        pending.append((experiment, config_path))

    with tempfile.TemporaryDirectory(prefix="simthesizer-batch-") as temp_dir:
        workspace_root = Path(temp_dir).resolve()
        create_workspace(workspace_root, pending)

        for experiment, config_path in pending:
            output_jsonl = result_jsonl_path(experiment, "simthesizer")
            output_jsonl.parent.mkdir(parents=True, exist_ok=True)

            typer.echo(f"[simthesizer] {experiment_name(experiment)}")
            workspace_config_path = prepare_workspace_config(
                workspace_root,
                config_path,
                override,
            )
            command = [
                "cargo",
                "run",
                "--release",
                "--quiet",
                "--",
                "--config",
                str(workspace_config_path),
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
