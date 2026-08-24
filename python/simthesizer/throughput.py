from __future__ import annotations

import json
import math
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path


class ThroughputError(Exception):
    pass


@dataclass(frozen=True)
class RequestTimeline:
    decode_token_times_sec: tuple[float, ...]


@dataclass
class TokenLatencyMismatchStats:
    rows: int = 0
    total_missing_token_latency: int = 0
    total_extra_token_latency: int = 0
    max_abs_diff: int = 0

    def record(self, diff: int) -> None:
        if diff == 0:
            return
        self.rows += 1
        self.max_abs_diff = max(self.max_abs_diff, abs(diff))
        if diff < 0:
            self.total_missing_token_latency += -diff
        else:
            self.total_extra_token_latency += diff

    def has_mismatches(self) -> bool:
        return self.rows > 0


@dataclass(frozen=True)
class ParsedSubRequest:
    kind: str
    output_tokens: int
    next_indices: tuple[int, ...]
    duration_sec: float | None = None
    token_latency_sec: tuple[float, ...] = ()


def compute_throughput_series(
    path: Path, window_sec: float
) -> tuple[list[float], list[float]]:
    rows = read_rows(path)
    max_time_sec = max(
        [0.0] + [time for row in rows for time in row.decode_token_times_sec]
    )
    return build_windowed_throughput(rows, window_sec, max_time_sec)


def align_series(
    left_times: list[float],
    left_values: list[float],
    right_times: list[float],
    right_values: list[float],
) -> tuple[list[float], list[float], list[float]]:
    if not left_times:
        left_times = [0.0]
        left_values = [0.0]
    if not right_times:
        right_times = [0.0]
        right_values = [0.0]

    if len(left_times) > 1:
        window_sec = left_times[1] - left_times[0]
    elif len(right_times) > 1:
        window_sec = right_times[1] - right_times[0]
    else:
        window_sec = 1.0

    max_len = max(len(left_times), len(right_times))
    times = [index * window_sec for index in range(max_len)]
    padded_left = left_values + [0.0 for _ in range(max_len - len(left_values))]
    padded_right = right_values + [0.0 for _ in range(max_len - len(right_values))]
    return times, padded_left, padded_right


def normalize_by_reference_peak(
    reference_values: list[float], candidate_values: list[float]
) -> tuple[list[float], list[float]]:
    scale = max([1.0] + reference_values)
    return (
        [value / scale for value in reference_values],
        [value / scale for value in candidate_values],
    )


def constrained_dtw_distance(
    reference_values: list[float],
    candidate_values: list[float],
    warp_window_bins: int,
) -> float:
    if warp_window_bins < 0:
        raise ThroughputError("warp window must be non-negative")

    if not reference_values and not candidate_values:
        return 0.0

    reference_values, candidate_values = normalize_by_reference_peak(
        reference_values, candidate_values
    )
    reference_len = len(reference_values)
    candidate_len = len(candidate_values)
    max_offset = abs(reference_len - candidate_len)
    band = max(warp_window_bins, max_offset)
    infinite_cost = math.inf

    costs = [
        [infinite_cost for _ in range(candidate_len + 1)]
        for _ in range(reference_len + 1)
    ]
    steps = [[0 for _ in range(candidate_len + 1)] for _ in range(reference_len + 1)]
    costs[0][0] = 0.0

    for reference_index in range(1, reference_len + 1):
        candidate_start = max(1, reference_index - band)
        candidate_end = min(candidate_len, reference_index + band)
        for candidate_index in range(candidate_start, candidate_end + 1):
            point_cost = abs(
                reference_values[reference_index - 1]
                - candidate_values[candidate_index - 1]
            )
            predecessor_cost, predecessor_steps = min(
                (
                    (
                        costs[reference_index - 1][candidate_index],
                        steps[reference_index - 1][candidate_index],
                    ),
                    (
                        costs[reference_index][candidate_index - 1],
                        steps[reference_index][candidate_index - 1],
                    ),
                    (
                        costs[reference_index - 1][candidate_index - 1],
                        steps[reference_index - 1][candidate_index - 1],
                    ),
                ),
                key=lambda item: item[0],
            )
            costs[reference_index][candidate_index] = predecessor_cost + point_cost
            steps[reference_index][candidate_index] = predecessor_steps + 1

    total_cost = costs[reference_len][candidate_len]
    total_steps = steps[reference_len][candidate_len]
    if not math.isfinite(total_cost) or total_steps == 0:
        raise ThroughputError("failed to compute DTW distance")
    return total_cost / total_steps


def read_rows(path: Path) -> list[RequestTimeline]:
    try:
        with path.open(encoding="utf-8") as handle:
            rows: list[RequestTimeline] = []
            mismatch_stats = TokenLatencyMismatchStats()
            for line_index, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    raw_row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ThroughputError(
                        f"failed to parse JSONL row {line_index} from {path}: {exc}"
                    ) from exc
                try:
                    timeline = parse_request_timeline(raw_row, mismatch_stats)
                except ThroughputError as exc:
                    raise ThroughputError(
                        f"failed to parse JSONL row {line_index} from {path}: {exc}"
                    ) from exc
                rows.append(timeline)
            warn_about_token_latency_mismatches(path, mismatch_stats)
            return rows
    except FileNotFoundError as exc:
        raise ThroughputError(f"failed to open JSONL {path}") from exc


def parse_request_timeline(
    row: object,
    mismatch_stats: TokenLatencyMismatchStats,
) -> RequestTimeline:
    if not isinstance(row, dict):
        raise ThroughputError("expected request object")

    arrival_sec = parse_non_negative_time(row.get("arrival_time"), "arrival_time")
    sub_requests_raw = row.get("sub_requests")
    if not isinstance(sub_requests_raw, list):
        raise ThroughputError("missing required field 'sub_requests'")
    initial_indices = parse_initial_indices(row.get("initial"), len(sub_requests_raw))

    sub_requests = [
        parse_sub_request(sub_request, index, len(sub_requests_raw), mismatch_stats)
        for index, sub_request in enumerate(sub_requests_raw)
    ]
    return build_request_timeline(arrival_sec, initial_indices, sub_requests)


def parse_sub_request(
    sub_request: object,
    index: int,
    total: int,
    mismatch_stats: TokenLatencyMismatchStats,
) -> ParsedSubRequest:
    if not isinstance(sub_request, dict):
        raise ThroughputError("sub_requests entries must be objects")

    kind = sub_request.get("kind")
    if kind not in {"llm", "tool_call"}:
        raise ThroughputError(f"unsupported sub-request kind {kind!r}")

    output_tokens = parse_non_negative_int(
        sub_request.get("output_tokens"), "output_tokens"
    )
    next_indices = parse_next_indices(sub_request.get("next"), total)

    if kind == "llm":
        token_latency_sec = parse_token_latency(sub_request.get("token_latency"))
        mismatch_stats.record(len(token_latency_sec) - output_tokens)
        return ParsedSubRequest(
            kind=kind,
            output_tokens=output_tokens,
            next_indices=next_indices,
            token_latency_sec=token_latency_sec,
        )

    duration_sec = parse_non_negative_time(sub_request.get("duration"), "duration")
    return ParsedSubRequest(
        kind=kind,
        output_tokens=output_tokens,
        next_indices=next_indices,
        duration_sec=duration_sec,
    )


def parse_non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ThroughputError(f"'{field}' must be an integer")
    if value < 0:
        raise ThroughputError(f"'{field}' must be non-negative")
    return value


def parse_non_negative_time(value: object, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ThroughputError(f"'{field}' must be numeric")
    parsed = float(value)
    if parsed < 0.0 or not math.isfinite(parsed):
        raise ThroughputError(f"'{field}' must be finite and non-negative")
    return parsed


def parse_next_indices(value: object, total: int) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise ThroughputError("'next' must be a list")

    parsed: list[int] = []
    for next_index in value:
        if isinstance(next_index, bool) or not isinstance(next_index, int):
            raise ThroughputError("'next' entries must be integers")
        if next_index < 0 or next_index >= total:
            raise ThroughputError("'next' entries must reference a valid sub-request")
        parsed.append(next_index)
    return tuple(parsed)


def parse_initial_indices(value: object, total: int) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise ThroughputError("missing required field 'initial'")

    parsed: list[int] = []
    seen: set[int] = set()
    for index in value:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ThroughputError("'initial' entries must be integers")
        if index < 0 or index >= total:
            raise ThroughputError(
                "'initial' entries must reference a valid sub-request"
            )
        if index in seen:
            raise ThroughputError("'initial' entries must not contain duplicates")
        seen.add(index)
        parsed.append(index)
    return tuple(parsed)


def parse_token_latency(value: object) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise ThroughputError("'token_latency' must be a list")

    parsed: list[float] = []
    for entry in value:
        if not isinstance(entry, (int, float)) or isinstance(entry, bool):
            raise ThroughputError("'token_latency' entries must be numeric")
        scaled = float(entry)
        if scaled < 0.0 or not math.isfinite(scaled):
            raise ThroughputError(
                "'token_latency' entries must be finite and non-negative"
            )
        parsed.append(scaled)
    return tuple(parsed)


def build_request_timeline(
    arrival_sec: float,
    initial_indices: tuple[int, ...],
    sub_requests: list[ParsedSubRequest],
) -> RequestTimeline:
    if not sub_requests:
        return RequestTimeline(decode_token_times_sec=())

    indegree = [0 for _ in sub_requests]
    ready_times = [arrival_sec for _ in sub_requests]
    completion_times = [0.0 for _ in sub_requests]
    decode_token_times: list[float] = []

    for sub_request in sub_requests:
        for next_index in sub_request.next_indices:
            indegree[next_index] += 1

    derived_initial = tuple(
        index for index, degree in enumerate(indegree) if degree == 0
    )
    if initial_indices != derived_initial:
        raise ThroughputError(
            f"'initial' {list(initial_indices)} does not match DAG roots {list(derived_initial)}"
        )

    queue = deque(initial_indices)
    visited = 0
    while queue:
        index = queue.popleft()
        visited += 1
        sub_request = sub_requests[index]
        ready_time = ready_times[index]

        if sub_request.kind == "llm":
            token_times, completion_time = build_llm_token_times(
                ready_time, sub_request
            )
            decode_token_times.extend(token_times)
        else:
            completion_time = ready_time + (sub_request.duration_sec or 0.0)

        completion_times[index] = completion_time
        for next_index in sub_request.next_indices:
            ready_times[next_index] = max(ready_times[next_index], completion_time)
            indegree[next_index] -= 1
            if indegree[next_index] == 0:
                queue.append(next_index)

    if visited != len(sub_requests):
        raise ThroughputError("request DAG contains a cycle")

    return RequestTimeline(decode_token_times_sec=tuple(decode_token_times))


def build_llm_token_times(
    ready_time: float, sub_request: ParsedSubRequest
) -> tuple[tuple[float, ...], float]:
    output_tokens = sub_request.output_tokens
    if output_tokens == 0:
        return (), ready_time
    if not sub_request.token_latency_sec:
        raise ThroughputError("llm sub-request is missing token_latency")

    effective_output_tokens = min(output_tokens, len(sub_request.token_latency_sec))
    token_times = [ready_time + sub_request.token_latency_sec[0]]
    for latency in sub_request.token_latency_sec[1:effective_output_tokens]:
        token_times.append(token_times[-1] + latency)
    return tuple(token_times), token_times[-1]


def warn_about_token_latency_mismatches(
    path: Path,
    mismatch_stats: TokenLatencyMismatchStats,
) -> None:
    if not mismatch_stats.has_mismatches():
        return

    print(
        "warning: adjusted token_latency/output token mismatches in "
        f"{path} ({mismatch_stats.rows} sub-request(s), "
        f"missing_token_latency={mismatch_stats.total_missing_token_latency}, "
        f"extra_token_latency={mismatch_stats.total_extra_token_latency}, "
        f"max_abs_diff={mismatch_stats.max_abs_diff})",
        file=sys.stderr,
    )


def build_windowed_throughput(
    rows: list[RequestTimeline], window_sec: float, max_time_sec: float
) -> tuple[list[float], list[float]]:
    if window_sec <= 0.0 or not math.isfinite(window_sec):
        raise ThroughputError("window size must be finite and positive")

    max_bin = max(0, math.ceil(max_time_sec / window_sec))
    times = [index * window_sec for index in range(max_bin + 1)]
    throughput = [0.0 for _ in times]

    for row in rows:
        for token_time_sec in row.decode_token_times_sec:
            if token_time_sec < 0.0 or not math.isfinite(token_time_sec):
                raise ThroughputError(
                    "decode token time must be finite and non-negative"
                )
            if token_time_sec == 0.0:
                bin_index = 0
            else:
                bin_index = min(math.ceil(token_time_sec / window_sec), max_bin)
            throughput[bin_index] += 1.0 / window_sec

    return times, throughput
