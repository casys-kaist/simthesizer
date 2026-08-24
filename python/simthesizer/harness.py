from __future__ import annotations

import socket
from pathlib import Path
from typing import Literal, TypeAlias

GIB = 1024**3
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = REPO_ROOT / "scripts"
OUTPUT_ROOT = REPO_ROOT / "output"
PROFILE_RECEIPTS_ROOT = REPO_ROOT / "profile_receipts"
PROFILE_DATA_ROOT = REPO_ROOT / "data"

RunnerName = Literal["baseline", "simthesizer", "real"]
Experiment = tuple[str, str]
ExperimentList: TypeAlias = list[Experiment]

EXPERIMENTS: ExperimentList = [
    ("single-gpu", "300"),
    ("single-gpu", "realistic"),
    ("multi-gpu", "300"),
    ("multi-gpu", "realistic"),
    ("multi-instance", "300"),
    ("multi-instance", "realistic"),
    ("pd-disaggregation", "realistic"),
    ("pd-disaggregation", "300"),
    ("moe", "realistic"),
    ("moe", "300"),
    ("mamba", "realistic"),
    ("mamba", "300"),
    # ("prefix-caching", "prefix-cache-highhit"),
    # ("prefix-caching", "sharegpt"),
    ("tool-call", "bfcl_gpt-5.4_n50_t0.0_sps5.0_seed42"),
    ("tool-call", "camel_gpt-5.4-nano_n25_t1.0_sps2.0_seed42"),
    ("tool-call", "swe-bench_gpt-5.4-nano_verified-n50_t0.0_sps2.0_seed42"),
    ("tool-call", "tau-bench_gpt-5.4_airline-n25+retail-n25_t0.0_sps5.0_seed42"),
]

# Optional per-host overrides layered on top of the scenario config. Empty by
# default: the bundled scenarios already describe the hardware the profiles in
# data/ were measured on, so mixing in another device's memory budget would pair
# those latencies with the wrong capacity.
#
# Add an entry to model a different budget. The key is matched against
# socket.gethostname(), or passed explicitly with --host.
#
#     HOST_OVERRIDES = {
#         "my-host": {"device_mem_bytes": 40 * GIB, "hardware": "A6000"},
#     }
HOST_OVERRIDES: dict[str, dict[str, object]] = {}

RUNNER_CONFIG_FILENAMES: dict[RunnerName, str] = {
    "baseline": "baseline.json",
    "simthesizer": "simthesizer.toml",
    "real": "real.yaml",
}


def iter_experiments() -> tuple[Experiment, ...]:
    return tuple(EXPERIMENTS)


def experiment_name(experiment: Experiment) -> str:
    scenario, workload = experiment
    return f"{scenario}-{workload}"


def experiment_output_dir(experiment: Experiment) -> Path:
    return OUTPUT_ROOT / experiment_name(experiment)


def workload_path(experiment: Experiment) -> Path:
    _, workload = experiment
    return REPO_ROOT / "workloads" / f"{workload}.jsonl"


def runner_config_path(experiment: Experiment, runner: RunnerName) -> Path | None:
    scenario, _ = experiment
    path = REPO_ROOT / "scenarios" / scenario / RUNNER_CONFIG_FILENAMES[runner]
    return path if path.is_file() else None


def result_jsonl_path(experiment: Experiment, runner: RunnerName) -> Path:
    return experiment_output_dir(experiment) / f"{runner}.jsonl"


def throughput_png_path(experiment: Experiment) -> Path:
    return experiment_output_dir(experiment) / "throughput.png"


def workload_request_count(path: Path) -> int:
    with path.open(encoding="utf-8") as file:
        return sum(1 for line in file if line.strip())


def resolve_host_override(host: str | None = None) -> tuple[str, dict[str, object]]:
    selected_host = host or socket.gethostname()
    if host is not None and selected_host not in HOST_OVERRIDES:
        raise SystemExit(f"host override not found: {selected_host}")
    return selected_host, dict(HOST_OVERRIDES.get(selected_host, {}))


def format_override_summary(override: dict[str, object]) -> str:
    if not override:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(override.items()))


def profile_receipt_path(model: str) -> Path:
    return PROFILE_RECEIPTS_ROOT / f"{model}.yaml"


def profile_output_path(model: str) -> Path:
    return PROFILE_DATA_ROOT / f"{model}.csv"
