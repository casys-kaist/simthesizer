import asyncio
import os
import time
from pathlib import Path

import typer
import yaml
from simthesizer.types import ReplayRequest, RequestResult, ResultSubRequest
from simthesizer.utils import parse_replay_workload, write_output_jsonl
from vllm import SamplingParams
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.sampling_params import RequestOutputKind
from vllm.v1.engine.async_llm import AsyncLLM


async def queue(
    engine: AsyncLLM,
    request: ReplayRequest,
    wait_time: float,
) -> RequestResult:
    await asyncio.sleep(wait_time)
    sub_request = request.sub_requests[0]

    sampling_params = SamplingParams(
        n=1,
        temperature=0.0,
        top_p=1.0,
        max_tokens=sub_request.output_tokens,
        ignore_eos=True,
        detokenize=False,
        output_kind=RequestOutputKind.DELTA,
    )

    token_latency: list[float] = []
    last_time = time.perf_counter()
    async for output in engine.generate(
        request_id=str(request.request_id),
        prompt=sub_request.input_token_ids,
        sampling_params=sampling_params,
    ):
        token_count = sum(len(completion.token_ids) for completion in output.outputs)
        if token_count == 0:
            continue
        cur_time = time.perf_counter()
        latency = (cur_time - last_time) / token_count
        token_latency.extend([latency] * token_count)
        last_time = cur_time

    if sub_request.output_tokens > 0 and not token_latency:
        raise RuntimeError("token_latency should have one entry per emitted token")
    return RequestResult(
        request_id=request.request_id,
        arrival_time=request.arrival_time,
        initial=request.initial,
        sub_requests=[
            ResultSubRequest(
                kind="llm",
                input_tokens=sub_request.input_tokens,
                output_tokens=sub_request.output_tokens,
                next=[],
                token_latency=token_latency,
            )
        ],
    )


async def run(
    engine: AsyncLLM,
    workload: list[ReplayRequest],
) -> list[RequestResult]:
    tasks = []
    sim_start = time.perf_counter()
    for req in workload:
        tasks.append(
            asyncio.create_task(
                queue(engine, req, sim_start + req.arrival_time - time.perf_counter())
            )
        )
    return await asyncio.gather(*tasks)


def main(
    config: Path = typer.Option(),
    workload: Path = typer.Option(),
    output_jsonl: Path = typer.Option(),
):
    workload = parse_replay_workload(workload.resolve())
    config_file = config.resolve()
    output_jsonl = output_jsonl.resolve()
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    with config_file.open() as f:
        cfg = yaml.safe_load(f)
    engine_args = AsyncEngineArgs(**cfg)
    engine = AsyncLLM.from_engine_args(engine_args)

    results = asyncio.run(run(engine, workload))

    engine.shutdown()
    write_output_jsonl(results, output_jsonl)


if __name__ == "__main__":
    typer.run(main)
