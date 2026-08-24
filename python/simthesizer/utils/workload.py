import json
from pathlib import Path
from typing import Any

from ..types import ReplayRequest, ReplaySubRequest


def parse_replay_workload(workload_path: Path) -> list[ReplayRequest]:
    if workload_path.suffix != ".jsonl":
        raise ValueError(
            f"unsupported replay workload format: {workload_path}; expected .jsonl"
        )

    requests = []
    with workload_path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{workload_path}:{line_number} expected JSON object")
            requests.append(parse_replay_request_row(workload_path, line_number, row))
    return requests


def parse_replay_request_row(
    workload_path: Path,
    line_number: int,
    row: dict[str, object],
) -> ReplayRequest:
    forbidden_keys = {"input_tokens", "output_tokens"} & row.keys()
    if forbidden_keys:
        raise ValueError(
            f"{workload_path}:{line_number} legacy top-level fields are not supported: "
            + ", ".join(sorted(forbidden_keys))
        )

    sub_requests = row.get("sub_requests")
    if not isinstance(sub_requests, list) or len(sub_requests) != 1:
        raise ValueError(
            f"{workload_path}:{line_number} expected a replayable workload with exactly one sub-request"
        )

    sub_request = sub_requests[0]
    if not isinstance(sub_request, dict) or sub_request.get("kind") != "llm":
        raise ValueError(
            f"{workload_path}:{line_number} expected a single llm sub-request"
        )

    initial = parse_initial_indices(workload_path, line_number, row.get("initial"))
    validate_initial_against_dag(workload_path, line_number, initial, sub_requests)

    raw_input_token_ids = sub_request.get("input_token_ids")
    if not isinstance(raw_input_token_ids, list):
        raise ValueError(
            f"{workload_path}:{line_number} missing sub_requests[0].input_token_ids required for replay"
        )

    input_token_ids = [int(token_id) for token_id in raw_input_token_ids]
    known_tokens = sub_request.get("known_tokens")
    kv_tokens = sub_request.get("kv_tokens")
    if known_tokens is not None or kv_tokens is not None:
        if not isinstance(known_tokens, int) or not isinstance(kv_tokens, int):
            raise ValueError(
                f"{workload_path}:{line_number} replayable llm sub-request seed fields must be integers"
            )
        if known_tokens != int(sub_request["input_tokens"]) or kv_tokens != 0:
            raise ValueError(
                f"{workload_path}:{line_number} replay only supports fresh llm sub-requests with known_tokens == input_tokens and kv_tokens == 0"
            )

    replay_sub_request = ReplaySubRequest(
        kind="llm",
        input_tokens=int(sub_request["input_tokens"]),
        output_tokens=int(sub_request["output_tokens"]),
        next=[int(index) for index in sub_request["next"]],
        input_token_ids=input_token_ids,
        output_token_ids=(
            [int(token_id) for token_id in raw_output_token_ids]
            if isinstance(
                raw_output_token_ids := sub_request.get("output_token_ids"), list
            )
            else None
        ),
        known_tokens=int(known_tokens) if known_tokens is not None else None,
        kv_tokens=int(kv_tokens) if kv_tokens is not None else None,
    )
    request = ReplayRequest(
        request_id=int(row["request_id"]),
        arrival_time=float(row["arrival_time"]),
        initial=initial,
        sub_requests=[replay_sub_request],
    )
    if len(replay_sub_request.input_token_ids) != replay_sub_request.input_tokens:
        raise ValueError(
            f"{workload_path}:{line_number} expected {replay_sub_request.input_tokens} input token ids, "
            f"found {len(replay_sub_request.input_token_ids)}"
        )
    return request


def build_canonical_workload(
    workload_path: Path,
    model: str | None = None,
) -> list[dict[str, object]]:
    if workload_path.suffix != ".jsonl":
        raise ValueError(f"unsupported workload format: {workload_path}")

    rows = []
    with workload_path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{workload_path}:{line_number} expected JSON object")
            rows.append((line_number, row))

    if not rows:
        return []

    if "sub_requests" in rows[0][1]:
        for line_number, row in rows:
            forbidden_keys = {"input_tokens", "output_tokens"} & row.keys()
            if forbidden_keys:
                raise ValueError(
                    f"{workload_path}:{line_number} legacy top-level fields are not supported: "
                    + ", ".join(sorted(forbidden_keys))
                )
            sub_requests = row.get("sub_requests")
            if not isinstance(sub_requests, list):
                raise ValueError(
                    f"{workload_path}:{line_number} missing required field 'sub_requests'"
                )
            initial = parse_initial_indices(
                workload_path, line_number, row.get("initial")
            )
            validate_initial_against_dag(
                workload_path, line_number, initial, sub_requests
            )
        return [row for _, row in rows]

    return [
        canonical_request_from_session(
            workload_path, line_number, session, request_id=index, model=model
        )
        for index, (line_number, session) in enumerate(rows)
    ]


def canonical_request_from_session(
    workload_path: Path,
    line_number: int,
    session: dict[str, object],
    request_id: int,
    model: str | None = None,
) -> dict[str, object]:
    events = session.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError(f"{workload_path}:{line_number} expected non-empty events")

    arrival_time = float(events[0]["timestamp"])
    sub_requests: list[dict[str, object]] = []

    for event in events:
        if not isinstance(event, dict):
            raise ValueError(f"{workload_path}:{line_number} expected event objects")

        event_type = event.get("event_type")
        if event_type == "request":
            llm_sub: dict[str, object] = {
                "kind": "llm",
                "input_tokens": int(event["input_tokens"]),
                "output_tokens": int(event["output_tokens"]),
                "next": [],
            }
            raw_interval = event.get("event_interval")
            if raw_interval is not None:
                llm_sub["interval"] = float(raw_interval) / 1000.0
            effective_model = model if model is not None else event.get("model")
            if effective_model is not None:
                llm_sub["model"] = effective_model
            sub_requests.append(llm_sub)
            continue

        if event_type == "tool_call":
            tc_sub: dict[str, object] = {
                "kind": "tool_call",
                "input_tokens": int(event.get("input_tokens") or 0),
                "output_tokens": int(event.get("output_tokens") or 0),
                "duration": float(event["duration_ms"]) / 1000.0,
                "next": [],
            }
            raw_interval = event.get("event_interval")
            if raw_interval is not None:
                tc_sub["interval"] = float(raw_interval) / 1000.0
            sub_requests.append(tc_sub)
            continue

        raise ValueError(
            f"{workload_path}:{line_number} unsupported event_type {event_type!r}"
        )

    for sub_request, next_indices in zip(
        sub_requests, build_consecutive_tool_dag(events), strict=True
    ):
        sub_request["next"] = next_indices

    initial = derive_initial_indices(sub_requests)

    return {
        "request_id": request_id,
        "arrival_time": arrival_time,
        "initial": initial,
        "sub_requests": sub_requests,
    }


def parse_initial_indices(
    workload_path: Path,
    line_number: int,
    raw_initial: object,
) -> list[int]:
    if not isinstance(raw_initial, list):
        raise ValueError(
            f"{workload_path}:{line_number} missing required field 'initial'"
        )

    initial: list[int] = []
    seen: set[int] = set()
    for index in raw_initial:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError(
                f"{workload_path}:{line_number} initial entries must be integers"
            )
        if index in seen:
            raise ValueError(
                f"{workload_path}:{line_number} initial entries must not contain duplicates"
            )
        seen.add(index)
        initial.append(index)
    return initial


def validate_initial_against_dag(
    workload_path: Path,
    line_number: int,
    initial: list[int],
    sub_requests: list[object],
) -> None:
    indegree = [0 for _ in sub_requests]
    for subrequest_index, sub_request in enumerate(sub_requests):
        if not isinstance(sub_request, dict):
            raise ValueError(
                f"{workload_path}:{line_number} sub_requests entries must be objects"
            )
        raw_next = sub_request.get("next")
        if not isinstance(raw_next, list):
            raise ValueError(
                f"{workload_path}:{line_number} sub_requests[{subrequest_index}] missing next list"
            )
        for next_index in raw_next:
            if isinstance(next_index, bool) or not isinstance(next_index, int):
                raise ValueError(
                    f"{workload_path}:{line_number} next entries must be integers"
                )
            if next_index < 0 or next_index >= len(sub_requests):
                raise ValueError(
                    f"{workload_path}:{line_number} next index {next_index} is out of range"
                )
            indegree[next_index] += 1

    for index in initial:
        if index < 0 or index >= len(sub_requests):
            raise ValueError(
                f"{workload_path}:{line_number} initial index {index} is out of range"
            )

    derived_initial = [index for index, degree in enumerate(indegree) if degree == 0]
    if initial != derived_initial:
        raise ValueError(
            f"{workload_path}:{line_number} initial {initial} does not match DAG roots {derived_initial}"
        )


def derive_initial_indices(sub_requests: list[dict[str, object]]) -> list[int]:
    indegree = [0 for _ in sub_requests]
    for sub_request in sub_requests:
        for next_index in sub_request["next"]:
            if not isinstance(next_index, int):
                raise ValueError("next entries must be integers")
            indegree[next_index] += 1
    return [index for index, degree in enumerate(indegree) if degree == 0]


def build_consecutive_tool_dag(events: list[object]) -> list[list[int]]:
    next_indices = [[] for _ in events]
    index = 0
    while index < len(events):
        event = events[index]
        if not isinstance(event, dict):
            raise ValueError("expected event objects")

        if event.get("event_type") != "request":
            if index + 1 < len(events):
                next_indices[index] = [index + 1]
            index += 1
            continue

        tool_start = index + 1
        tool_end = tool_start
        while tool_end < len(events):
            tool_event = events[tool_end]
            if (
                not isinstance(tool_event, dict)
                or tool_event.get("event_type") != "tool_call"
            ):
                break
            tool_end += 1

        if tool_start < tool_end:
            next_indices[index] = list(range(tool_start, tool_end))
            if tool_end < len(events):
                for tool_index in range(tool_start, tool_end):
                    next_indices[tool_index] = [tool_end]
            index = tool_end
            continue

        if index + 1 < len(events):
            next_indices[index] = [index + 1]
        index += 1

    return next_indices


def write_canonical_workload(rows: list[dict[str, Any]], workload_path: Path) -> None:
    with workload_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row))
            file.write("\n")


def write_baseline_dataset(requests: list[ReplayRequest], workload_path: Path) -> None:
    with workload_path.open("w", encoding="utf-8") as file:
        for request in requests:
            row = {
                "request_id": request.request_id,
                "input_toks": request.sub_requests[0].input_tokens,
                "output_toks": request.sub_requests[0].output_tokens,
                "arrival_time_ns": int(round(request.arrival_time * 1_000_000_000.0)),
                "input_tok_ids": request.sub_requests[0].input_token_ids,
            }
            file.write(json.dumps(row))
            file.write("\n")
