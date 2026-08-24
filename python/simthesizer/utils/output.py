import json
from pathlib import Path

from ..types import RequestResult, ResultSubRequest


def sub_request_to_row(sub_request: ResultSubRequest) -> dict[str, object]:
    row: dict[str, object] = {
        "kind": sub_request.kind,
        "input_tokens": sub_request.input_tokens,
        "output_tokens": sub_request.output_tokens,
        "next": sub_request.next,
    }
    if sub_request.kind == "llm":
        if sub_request.token_latency is None:
            raise ValueError("llm sub-request results require token_latency")
        row["token_latency"] = sub_request.token_latency
        return row

    if sub_request.duration is None:
        raise ValueError("tool_call sub-request results require duration")
    row["duration"] = sub_request.duration
    return row


def request_result_to_row(result: RequestResult) -> dict[str, object]:
    return {
        "request_id": result.request_id,
        "arrival_time": result.arrival_time,
        "initial": result.initial,
        "sub_requests": [
            sub_request_to_row(sub_request) for sub_request in result.sub_requests
        ],
    }


def write_output_jsonl(results: list[RequestResult], output_jsonl: Path) -> None:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as file:
        for result in sorted(results, key=lambda result: result.request_id):
            file.write(json.dumps(request_result_to_row(result)))
            file.write("\n")
