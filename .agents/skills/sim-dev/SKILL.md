---
name: sim-dev
description: Use when implementing, modifying, or debugging simulator behavior in this Simthesizer repository, especially under crates/simthesizer-core/. This includes adding or changing schedulers, models, model runners, systems, compute simulators, network simulators, scenario configs in simthesizer.toml, validating simulation output against real system data, and diagnosing discrepancies between simulated and real system behavior. Use before writing Rust code that changes simulator logic.
---

# Simulator Development

Guardrails for changing simulation behavior. Read `ARCHITECTURE.md` first — it
states the invariants the engine relies on.

## Before writing code

You need concrete values for all of these. If any is missing, **ask the user
now**. If the user does not know, find the value in official documentation or
source and state which value you adopted and why. Never assume silently.

- **Hardware** — device model, memory per device, device count, interconnect
  type and topology
- **Serving config** — parallelism degree, context length, batch and token
  budgets, block size, prefix-caching and chunked-prefill flags
- **Model config** — layer, head, and dimension counts, plus any
  architecture-specific field that changes tensor width or KV size
- **Workload** — arrival pattern, length distribution, and token-id traces if
  prefix reuse matters
- **Metric semantics** — what each output field means, at what granularity, and
  whether the exported view differs from internal state

Correct logic with wrong parameters produces confident wrong numbers, and wrong
parameters are the more common failure. A plausible-looking result is not
evidence that the configuration is right.

## Rules

### Inspect the real artifact, never the idealized one

Open the actual file and read its real schema before coding against it. Field
names in real artifacts are frequently legacy labels whose plain-English reading
no longer matches what the field stores — cross-check a field's values against
related fields to confirm what it actually represents.

Treat artifact loaders as compatibility boundaries: parse defensively, tolerate
missing optional sections, and normalize legacy shapes once at the IO boundary
rather than inside simulation logic.

### Separate internal state from exported metrics

A simulator can be mechanism-correct internally and still score badly because
its exported view does not match the metric's definition. When the two differ,
keep both and document the mapping explicitly.

### Compare like-for-like

Before comparing against a reference run, establish its completion policy —
finish-all-requests, fixed-duration, or truncated. A truncated reference is not
comparable to a simulation that completes every request.

### Model the mechanism, not a proxy

Implement the actual behavior before falling back to a statistical
approximation.

| Instead of | Implement |
|---|---|
| A hit-rate parameter | The cache's actual lookup and eviction |
| A speedup factor | The steps that produce the speedup |
| An assumed-infinite resource | Allocation and reclamation of the real resource |
| A fixed per-phase latency | The budget the real system spends across the batch |

A proxy is a last resort. Propose it to the user first, and document it where a
reader will see it.

### Surface underspecified costs

When a latency or capacity component is not specified, expose it as a config
knob or a documented assumption. Do not bury it as a constant in the code.

### Reuse before rebuild

In order: extend an existing implementation, then compose within existing
modules via the trait and `register_*!` macros, then create a new module. Code
that bypasses the architecture's invariants may compile while breaking
assumptions the rest of the system depends on.

### Add models properly

Check `crates/simthesizer-core/src/models/` first. If the model is absent, get
its real config from an authoritative source and implement it against the
`Model` trait — never alias another model, however similar the architecture
looks. Confirm profiling data exists for this model and hardware pair; data from
a different model is not a substitute.

If the model already exists but the task depends on shape-sensitive quantities,
re-read the real config rather than deriving dimensions that may be stated
explicitly.

### Model interconnect from measured bandwidth

Topology has an outsized effect on accuracy. Effective bandwidth under
contention can fall far below theoretical peak, and the gap widens with the
number of participating devices, so a link shared through a switch behaves
nothing like a direct peer-to-peer link. `link_bw` in `simthesizer.toml` must
reflect measured or realistic effective bandwidth.

## When simulation diverges from a real run

Diagnose in this order. Skipping ahead wastes the most time.

1. `simthesizer.toml` hardware parameters match the target system.
2. Model and scheduler config match the target's launch config.
3. Profiled compute data was generated under matching conditions.
4. Only then, look for unmodeled overhead.

## Checklist

- [ ] Target system detail is complete; nothing was assumed silently.
- [ ] Real artifacts were opened and their actual schemas confirmed.
- [ ] Metric semantics are explicit, including any internal-vs-exported split.
- [ ] Reference-run completion policy is known and comparable.
- [ ] Existing code was surveyed before new code was written.
- [ ] The mechanism is implemented, not approximated.
- [ ] Underspecified costs are config knobs or documented assumptions.
- [ ] Interconnect reflects effective, not theoretical, bandwidth.
- [ ] `ARCHITECTURE.md` invariants still hold.
