from dataclasses import dataclass


@dataclass
class ReplaySubRequest:
    kind: str
    input_tokens: int
    output_tokens: int
    next: list[int]
    input_token_ids: list[int]
    output_token_ids: list[int] | None = None
    known_tokens: int | None = None
    kv_tokens: int | None = None


@dataclass
class ReplayRequest:
    request_id: int
    arrival_time: float
    initial: list[int]
    sub_requests: list[ReplaySubRequest]


@dataclass
class ResultSubRequest:
    kind: str
    input_tokens: int
    output_tokens: int
    next: list[int]
    duration: float | None = None
    token_latency: list[float] | None = None


@dataclass
class RequestResult:
    request_id: int
    arrival_time: float
    initial: list[int]
    sub_requests: list[ResultSubRequest]
