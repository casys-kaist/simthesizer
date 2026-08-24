import csv
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import typer
from simthesizer.paths import absolute_from_cwd, load_json_object
from simthesizer.types import RequestResult, ResultSubRequest
from simthesizer.utils import (
    parse_replay_workload,
    write_baseline_dataset,
    write_output_jsonl,
)

NS_TO_SECONDS = 1e-9
RAW_REQUIRED_FIELDS = {
    "request id",
    "input",
    "output",
    "arrival",
    "TTFT",
    "ITL",
}


def validate_scenario(scenario: dict[str, Any], scenario_path: Path) -> None:
    missing = [
        key
        for key in ("cluster_config", "num_req")
        if key not in scenario or scenario[key] in (None, "")
    ]
    if missing:
        raise SystemExit(
            f"{scenario_path} is missing required keys: {', '.join(missing)}"
        )
    if not isinstance(scenario["cluster_config"], dict):
        raise SystemExit(f"{scenario_path} must embed cluster_config as a JSON object")


def relative_path(from_dir: Path, to_path: Path) -> str:
    return os.path.relpath(to_path, from_dir)


def terminate_process_group(process: subprocess.Popen[str], signum: int) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        return


def kill_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return

    terminate_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass

    terminate_process_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_llmservingsim(
    llmservingsim_root: Path,
    scenario: dict[str, Any],
    dataset_path: Path,
    output_csv: Path,
) -> None:
    chakra_pythonpath = str(
        llmservingsim_root / "astra-sim" / "extern" / "graph_frontend"
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        prefix="simthesizer-baseline-cluster-",
        dir=llmservingsim_root,
        encoding="utf-8",
        delete=False,
    ) as cluster_file:
        json.dump(scenario["cluster_config"], cluster_file)
        cluster_config_path = Path(cluster_file.name)

    command = [sys.executable, "main.py"]
    for key, value in scenario.items():
        if value is None or key in {"cluster_config", "dataset", "output"}:
            continue
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool):
            if value:
                command.append(flag)
            continue
        command.extend([flag, str(value)])
    command.extend(
        [
            "--cluster-config",
            relative_path(llmservingsim_root, cluster_config_path),
            "--dataset",
            relative_path(llmservingsim_root, dataset_path),
            "--output",
            relative_path(llmservingsim_root, output_csv),
        ]
    )

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{chakra_pythonpath}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else chakra_pythonpath
    )

    process = subprocess.Popen(
        command,
        cwd=llmservingsim_root,
        env=env,
        text=True,
        start_new_session=True,
    )
    previous_handlers: dict[int, Any] = {}

    def handle_interrupt(signum: int, _frame: Any) -> None:
        terminate_process_group(process, signum)
        raise SystemExit(128 + signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, handle_interrupt)

        try:
            returncode = process.wait()
        except BaseException:
            kill_process_group(process)
            raise
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)

        if returncode != 0:
            raise SystemExit(f"llmservingsim exited with code {returncode}")
    finally:
        cluster_config_path.unlink(missing_ok=True)


def parse_itl(raw_itl: str) -> list[float]:
    try:
        parsed = json.loads(raw_itl)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"failed to parse ITL list from baseline output: {raw_itl}"
        ) from exc
    if not isinstance(parsed, list):
        raise SystemExit(f"baseline ITL column is not a list: {raw_itl}")
    try:
        return [float(value) * NS_TO_SECONDS for value in parsed]
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            f"baseline ITL column contains non-numeric values: {raw_itl}"
        ) from exc


def parse_result(row: dict[str, str], initial: list[int]) -> RequestResult:
    try:
        request_id = int(row["request id"])
        input_tokens = int(row["input"])
        total_output_tokens = int(row["output"])
        arrival_time = float(row["arrival"]) * NS_TO_SECONDS
        ttft = float(row["TTFT"]) * NS_TO_SECONDS
    except ValueError as exc:
        raise SystemExit(
            f"baseline output contains invalid numeric values: {row}"
        ) from exc

    output_tokens = total_output_tokens - input_tokens
    if output_tokens < 0:
        raise SystemExit(
            "baseline output is invalid: output token count is smaller than input token count"
        )

    token_latency = [ttft, *parse_itl(row["ITL"])] if output_tokens > 0 else []

    return RequestResult(
        request_id=request_id,
        arrival_time=arrival_time,
        initial=initial,
        sub_requests=[
            ResultSubRequest(
                kind="llm",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                next=[],
                token_latency=token_latency,
            )
        ],
    )


def load_results(
    raw_output_path: Path, initial_by_request_id: dict[int, list[int]]
) -> list[RequestResult]:
    if not raw_output_path.is_file():
        raise SystemExit(
            f"baseline simulator output was not created: {raw_output_path}"
        )

    with raw_output_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        missing = RAW_REQUIRED_FIELDS - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(
                f"baseline output is missing required columns: {', '.join(sorted(missing))}"
            )
        results = []
        for row in reader:
            request_id = int(row["request id"])
            initial = initial_by_request_id.get(request_id)
            if initial is None:
                raise SystemExit(
                    f"baseline output references unknown request_id {request_id}"
                )
            results.append(parse_result(row, initial))
        return results


def main(
    config: Path = typer.Option(),
    workload: Path = typer.Option(),
    output_jsonl: Path = typer.Option(),
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    llmservingsim_root = (repo_root / "extern" / "llmservingsim").resolve()
    if not llmservingsim_root.is_dir():
        raise SystemExit(f"llmservingsim repository not found: {llmservingsim_root}")

    config = absolute_from_cwd(config).resolve()
    workload = absolute_from_cwd(workload).resolve()
    output_jsonl = absolute_from_cwd(output_jsonl).resolve()
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    scenario = load_json_object(config)
    validate_scenario(scenario, config)
    requests = parse_replay_workload(workload)
    initial_by_request_id = {
        request.request_id: request.initial for request in requests
    }

    with tempfile.TemporaryDirectory(prefix="simthesizer-baseline-") as temp_dir:
        dataset_path = Path(temp_dir) / "workload.jsonl"
        raw_output_csv = Path(temp_dir) / "baseline-output.csv"
        write_baseline_dataset(requests, dataset_path)
        run_llmservingsim(llmservingsim_root, scenario, dataset_path, raw_output_csv)

        results = load_results(raw_output_csv, initial_by_request_id)

    write_output_jsonl(results, output_jsonl)


if __name__ == "__main__":
    try:
        typer.run(main)
    except KeyboardInterrupt:
        raise SystemExit(130)
