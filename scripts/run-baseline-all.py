import json
import subprocess
import sys
import tempfile
from pathlib import Path

import typer
from simthesizer.harness import (
    GIB,
    REPO_ROOT,
    SCRIPT_ROOT,
    Experiment,
    experiment_name,
    format_override_summary,
    iter_experiments,
    resolve_host_override,
    result_jsonl_path,
    runner_config_path,
    workload_path,
    workload_request_count,
)


def apply_overrides(
    scenario: dict[str, object],
    override: dict[str, object],
    request_count: int,
) -> dict[str, object]:
    scenario = json.loads(json.dumps(scenario))
    scenario["num_req"] = request_count

    device_mem_bytes = override.get("device_mem_bytes")
    hardware = override.get("hardware")
    if device_mem_bytes is None and hardware is None:
        return scenario
    if device_mem_bytes is not None and not isinstance(device_mem_bytes, int):
        raise SystemExit(
            f"baseline device_mem_bytes must be an integer: {device_mem_bytes}"
        )
    if hardware is not None and not isinstance(hardware, str):
        raise SystemExit(f"baseline hardware must be a string: {hardware}")
    if isinstance(device_mem_bytes, int) and device_mem_bytes % GIB != 0:
        raise SystemExit(
            f"baseline device_mem_bytes must be divisible by {GIB}: {device_mem_bytes}"
        )

    cluster_config = scenario.get("cluster_config")
    if not isinstance(cluster_config, dict):
        raise SystemExit("baseline scenario must contain cluster_config")

    nodes = cluster_config.get("nodes")
    if not isinstance(nodes, list):
        raise SystemExit("baseline cluster_config.nodes must be a list")

    mem_size_gib = (
        device_mem_bytes // GIB if isinstance(device_mem_bytes, int) else None
    )
    for node in nodes:
        if not isinstance(node, dict):
            raise SystemExit("baseline cluster_config.nodes entries must be objects")
        instances = node.get("instances")
        if not isinstance(instances, list):
            raise SystemExit("baseline cluster node instances must be a list")
        for instance in instances:
            if not isinstance(instance, dict):
                raise SystemExit("baseline instances entries must be objects")
            if mem_size_gib is not None:
                npu_mem = instance.get("npu_mem")
                if not isinstance(npu_mem, dict):
                    raise SystemExit("baseline instance npu_mem must be an object")
                npu_mem["mem_size"] = mem_size_gib
            if hardware is not None:
                instance["hardware"] = hardware

    return scenario


def write_temp_config(
    temp_root: Path,
    experiment: Experiment,
    config_path: Path,
    override: dict[str, object],
) -> Path:
    with config_path.open(encoding="utf-8") as file:
        scenario = json.load(file)
    if not isinstance(scenario, dict):
        raise SystemExit(
            f"baseline scenario file must contain a JSON object: {config_path}"
        )

    output_path = temp_root / f"{experiment_name(experiment)}.json"
    overridden = apply_overrides(
        scenario,
        override,
        workload_request_count(workload_path(experiment)),
    )
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(overridden, file, indent=2)
        file.write("\n")
    return output_path


def main(
    host: str | None = typer.Option(None, "--host"),
    full: bool = typer.Option(False, "--full"),
) -> None:
    selected_host, override = resolve_host_override(host)
    typer.echo(f"host={selected_host} override={format_override_summary(override)}")

    succeeded = 0
    failed = 0
    skipped = 0
    pending = []
    for experiment in iter_experiments():
        config_path = runner_config_path(experiment, "baseline")
        if config_path is None:
            typer.echo(f"skip: {experiment_name(experiment)} (no baseline config)")
            skipped += 1
            continue
        output_jsonl = result_jsonl_path(experiment, "baseline")
        if not full and output_jsonl.is_file():
            typer.echo(f"skip: {experiment_name(experiment)} ({output_jsonl} exists)")
            skipped += 1
            continue
        pending.append((experiment, config_path))

    with tempfile.TemporaryDirectory(prefix="simthesizer-baseline-") as temp_dir:
        temp_root = Path(temp_dir).resolve()
        for experiment, config_path in pending:
            output_jsonl = result_jsonl_path(experiment, "baseline")
            output_jsonl.parent.mkdir(parents=True, exist_ok=True)
            prepared_config = write_temp_config(
                temp_root,
                experiment,
                config_path,
                override,
            )

            typer.echo(f"[baseline] {experiment_name(experiment)}")
            command = [
                sys.executable,
                str(SCRIPT_ROOT / "run-baseline.py"),
                "--config",
                str(prepared_config),
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
