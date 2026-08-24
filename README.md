<h1 align="center">Simthesizer</h1>

<p align="center">
  <strong>An agent-driven, discrete-event simulator for LLM serving systems.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
  <img alt="Rust" src="https://img.shields.io/badge/Rust-stable-CE422B?logo=rust&logoColor=white">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="No GPU required" src="https://img.shields.io/badge/simulation-no%20GPU%20required-2EA043">
</p>

*Agent-driven* in two senses — the workloads it runs, and the way it is built.

**It runs agent workloads natively.** Most serving simulators replay a stream of
independent requests. Agent workloads do not look like that. One user task
expands into a dependency graph of LLM calls and tool calls: each step's prompt
is built from everything that came before, the GPU goes idle while a tool runs,
and the session holds KV cache the whole time. Simthesizer takes that graph as
its native input, so scheduling, prefix reuse, and KV residency are simulated
against the shape real agents actually produce.

**It is built to be extended by coding agents.** Every component is reached
through an open registry keyed by a `kind` string, so adding a scheduler or a
model is a self-contained change with no central file to edit and no merge
conflict to resolve. The repository ships the agent skills used to develop it —
`sim-dev` for modeling guardrails, `task-design` for writing specs an
implementation agent can execute — and both Codex and Claude Code pick them up
automatically on clone. `ARCHITECTURE.md` states the invariants in falsifiable
form so an agent can check its own work against them.

```bash
git clone https://github.com/casys-kaist/Simthesizer.git
cd Simthesizer
cargo run --release -- \
  --config scenarios/single-gpu/simthesizer.toml \
  --workload workloads/sharegpt.jsonl \
  --output-jsonl out.jsonl
```

That works on a clean clone with no GPU and no setup step. The profiler tables
the bundled scenarios need ship in `data/`.

---

## 🔗 Agent-driven workloads

One agent session, as the simulator sees it:

```text
  request 0                                    arrival 3.716 s

  +-----------+   +-----------+   +-----------+   +-----------+
  | 1  llm    |   | 2  llm    |   | 3  tool   |   | 4  llm    |
  | in   2710 |-->| in     97 |-->| 0.42 s    |-->| in    325 |--> ...
  | out    59 |   | out    23 |   | no GPU    |   | out    33 |
  +-----------+   +-----------+   +-----------+   +-----------+
                   think 0.89 s

  GPU  ##########............###....................####    # busy  . idle
  KV   ==================================================    = blocks held

  The scheduler can admit other sessions during the idle stretches,
  but this session's KV blocks are never released.
```

That interleaving is the whole point. A flat request list cannot express it,
so a simulator fed flat requests will under-count KV pressure and over-count
available GPU time.

Each line of a workload JSONL is one request. `sub_requests` is a DAG linked by
`next` indices; `initial` lists the roots that fire at arrival time.

```json
{
  "request_id": 0,
  "arrival_time": 3.716,
  "initial": [0],
  "sub_requests": [
    { "kind": "llm",       "input_tokens": 2710, "output_tokens": 59, "next": [1], "interval": 0.0 },
    { "kind": "llm",       "input_tokens": 97,   "output_tokens": 23, "next": [2], "interval": 0.893 },
    { "kind": "tool_call", "duration": 0.42,     "input_tokens": 0,   "output_tokens": 0, "next": [3], "interval": 0.0 },
    { "kind": "llm",       "input_tokens": 325,  "output_tokens": 33, "next": [4], "interval": 0.0003 }
  ]
}
```

What this buys you over a flat request list:

- **Tool-call gaps are first class.** A `tool_call` node occupies wall-clock
  time without occupying the GPU — the scheduler sees the slot free up while
  the session's KV cache stays resident.
- **Every step carries its own full context.** `input_tokens` on each `llm`
  node is the complete prompt for that step, so prefix growth across a session
  is explicit rather than inferred.
- **Think time is modeled.** `interval` is the delay before a node starts once
  its predecessors finish.
- **Optional token-id traces.** `input_token_ids` / `output_token_ids` on an
  `llm` node drive block-hash prefix caching against real token sequences
  instead of a hit-rate parameter.

Bundled workloads, all in this format:

| Workload | Requests | Token IDs | Source |
|---|---:|---|---|
| `tau-bench_gpt-5.4_airline-n25+retail-n25_…` | 49 | – | τ-bench, airline + retail |
| `swe-bench_gpt-5.4-nano_verified-n50_…` | 49 | – | SWE-bench Verified |
| `bfcl_gpt-5.4_n50_…` | 50 | – | Berkeley Function-Calling Leaderboard |
| `camel_gpt-5.4-nano_n25_…` | 25 | – | CAMEL multi-agent |
| `sharegpt.jsonl` | 400 | yes | ShareGPT conversations |
| `realistic.jsonl` | 100 | yes | Hand-built, mixed lengths |
| `300.jsonl` | 300 | yes | Hand-built |
| `prefix-cache-highhit.jsonl` | 100 | yes | Hand-built for high prefix reuse |

Token IDs drive block-hash prefix caching. The agent workloads carry token
counts and timing only.

See [`workloads/README.md`](workloads/README.md) for the full schema.

## 🧱 What you can model

Every component below is selected by a `kind` string in the scenario TOML.

| Registry | Available |
|---|---|
| System | `single_instance`, `multi_instance`, `pd_disaggregation`, `moe_ep_multi_instance` |
| Model | `llama3_1`, `qwen3_moe`, `nemotron_h` (hybrid Mamba–attention) |
| Model runner | `ring` (tensor parallel), `moe_ep` (expert parallel) |
| Scheduler | `chunked_prefill` — vLLM-style, two-phase running/waiting pools, block-hash prefix cache with LRU eviction |
| Compute simulator | `table_lookup` — interpolates measured per-layer latency from a profiler CSV |
| Network simulator | `flow` |
| Router | `round_robin`, `moe_load_balancing` |

## 📦 Bundled scenarios

| Scenario | System | Model | Notes |
|---|---|---|---|
| `single-gpu` | `single_instance` | Llama 3.1 8B | TP1 baseline |
| `multi-gpu` | `single_instance` | Llama 3.1 8B | TP2 |
| `multi-instance` | `multi_instance` | Llama 3.1 8B | Two replicas behind a router |
| `pd-disaggregation` | `pd_disaggregation` | Llama 3.1 8B | Split prefill / decode workers |
| `prefix-caching` | `single_instance` | Llama 3.1 8B | `enable_prefix_caching = true` |
| `tool-call` | `multi_instance` | Llama 3.1 8B | Agent workloads across two replicas |
| `moe` | `moe_ep_multi_instance` | Qwen3 30B-A3B | Expert parallel |
| `mamba` | `moe_ep_multi_instance` | Nemotron-H | Hybrid Mamba–attention, 52 layers |

Run any of them against any workload:

```bash
cargo run --release -- \
  --config scenarios/tool-call/simthesizer.toml \
  --workload workloads/tau-bench_gpt-5.4_airline-n25+retail-n25_t0.0_sps5.0_seed42.jsonl \
  --output-jsonl out.jsonl
```

## 📊 Output

One JSONL row per request, preserving the input DAG and attaching timing to
each sub-request:

```json
{
  "request_id": 0,
  "arrival_time": 0.0469,
  "initial": [0],
  "sub_requests": [
    { "kind": "llm", "input_tokens": 25, "output_tokens": 619, "next": [],
      "token_latency": [0.02257, 0.01095, 0.01095, "..."] }
  ]
}
```

`token_latency[0]` is TTFT; `token_latency[1..]` are inter-token latencies.
Because the DAG shape is preserved, you can attribute latency to a specific
step of an agent session rather than to the session as a whole.

## 🔌 Extending, with or without an agent

Registration is open across crate boundaries. A downstream crate can add a
component without editing this repo's central code — depend on
`simthesizer-core`, derive `Deserialize`, and register:

```rust
use simthesizer_core::{register_scheduler, Scheduler};

#[derive(serde::Deserialize)]
struct MyScheduler { /* config fields */ }

impl Scheduler for MyScheduler { /* ... */ }

register_scheduler!(MyScheduler, "my_scheduler");
```

It is then selectable as `kind = "my_scheduler"` in any scenario TOML. The same
pattern applies to `register_system!`, `register_model!`, `register_router!`,
`register_model_runner!`, `register_compute_simulator!`, and
`register_network_simulator!`.

Read [`ARCHITECTURE.md`](ARCHITECTURE.md) before changing core structure — it
records the invariants the engine relies on.

### 🤖 Handing the work to Codex or Claude

That registry shape is what makes this tractable for a coding agent: a new
component touches one directory and one macro call, so the agent never has to
hold the whole system in context or edit a file another change is also editing.

The skills that were used to build this simulator ship with it, in
`.agents/skills/`:

- **`sim-dev`** — modeling guardrails. What to verify about a model config,
  why a statistical proxy is a last resort, how to check hardware topology
  before blaming the profiler. Read before changing
  `crates/simthesizer-core/`.
- **`task-design`** — how to write a task spec an implementation agent can
  execute correctly on the first attempt, including the worked computation
  trace that catches off-by-one and off-by-factor modeling errors.

Codex reads `.agents/skills/` directly. Claude Code picks them up through the
`.claude/skills` symlink — both point at the same files, so there is one copy
to maintain. Neither needs any setup beyond cloning.

## 📐 Profiling your own hardware

`table_lookup` reads a CSV of measured per-layer latencies. Three are bundled
(`data/llama3_1_8b.csv`, `data/qwen3_30b_a3b.csv`,
`data/nemotron3_nano_30b_a3b.csv`), all measured on an NVIDIA RTX PRO 6000
Blackwell Server Edition — see the header of each file in `profile_receipts/`.
Latencies are device-specific, so re-profile before trusting them elsewhere:

```bash
uv run python scripts/vllm-profile.py \
  --receipt profile_receipts/llama3_1_8b.yaml \
  --output-csv data/llama3_1_8b.csv
```

A receipt declares which layers to sample and over what shape grid. This step
needs a GPU and a vLLM install; simulation itself does not.

## 🎯 Validating against a real system

Simulation results are only worth what they were checked against. The repo
ships the harness used to check them — three runners over the same
`(scenario, workload)` pair, then a comparison:

```text
  scenarios/<name>/
  +------------------+
  | simthesizer.toml |--> Simthesizer       --+
  | baseline.json    |--> LLMServingSim     --+--> compare-all.py -> DTW distance
  | real.yaml        |--> real vLLM on GPUs --+    draw-all.py    -> throughput.png
  +------------------+
```

```bash
uv run python scripts/run-simthesizer-all.py   # this simulator
uv run python scripts/run-baseline-all.py      # LLMServingSim
uv run python scripts/run-real-all.py          # real vLLM
uv run python scripts/compare-all.py           # constrained DTW
uv run python scripts/draw-all.py              # throughput plots
```

No host flag is needed: the bundled scenarios already describe the hardware the
profiles were measured on. To model a different memory budget, add an entry to
`HOST_OVERRIDES` in `python/simthesizer/harness.py` and pass `--host <key>`.

Each runner reads a conventional filename inside a scenario directory —
`simthesizer.toml`, `baseline.json`, `real.yaml` — and writes to
`output/<scenario>-<workload>/`.

The baseline runner needs a one-time setup that clones and patches
[LLMServingSim](https://github.com/casys-kaist/LLMServingSim):

```bash
./scripts/baseline-setup.sh
```

The real runner needs GPUs and a vLLM install.

## 📁 Repository layout

```
crates/simthesizer-core/   Engine, systems, models, runners, schedulers, simulators
crates/simthesizer-cli/    CLI entrypoint and result JSONL writer
scenarios/<name>/          Runner configs: simthesizer.toml, plus baseline.json / real.yaml where applicable
workloads/                 Canonical workload JSONL
data/                      Profiler CSVs consumed by table_lookup
profile_receipts/          Declarative profiling recipes
scripts/                   Profiling, batch runners, comparison, plotting
python/simthesizer/        Shared helpers for the scripts
.agents/skills/            sim-dev and task-design skills, shared by Codex and Claude
```

## ⚙️ Requirements

Simulation only:

- `cargo` (Rust stable)

Profiling, batch runners, and comparison:

- `uv`, Python 3.12
- `cmake`, `protobuf` — baseline simulator
- CUDA Toolkit 12.8, `rdma-core` — real runs

Or use the pinned Nix environment:

```bash
devenv shell
```

## 📄 License

MIT. See [LICENSE](LICENSE).
