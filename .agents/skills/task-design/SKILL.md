---
name: task-design
description: Use when writing or reviewing a task spec that will be given to an implementation agent. Guides task design through interactive Q&A so the agent can succeed on the first attempt without relying on general harness principles.
---

# Task Design

Write a spec an implementation agent can execute correctly on the first attempt.
This is an interactive conversation: research what you can, ask about what you
cannot, draft, and review with the user as you go.

## What belongs in a spec

**Task-specific facts** — intent, scope, metric definitions, mechanism
semantics. These are things the implementation agent cannot know without being
told.

Generic implementation guardrails — verifying configs, avoiding statistical
proxies, modeling topology, reusing modules — belong in `sim-dev` and run at
implementation time. A spec that restates them absorbs the harness's role and
grows without adding information.

The repository is a discrete-event simulator. A task may model existing
real-system behavior, a novel mechanism with no real-world counterpart, or a
mix. In every case the spec must **stand alone**: when the user cites an
external system, extract the semantics the simulator needs and write them out as
definitions. Do not link to external source and expect the agent to derive
intent from it — that code can change, and the agent will over-emulate it.

## Information boundaries

### Get from the user — never infer

1. **Feature intent** — what to build and why it matters
2. **Target hardware and serving config** — stated explicitly, or a specific
   config file the user names
3. **Workload** — which one to use
4. **Expected simulator output** — the metric, its precise definition, its
   granularity, and any internal-vs-exported semantic split
5. **Scope boundary** — expected realism versus what is out of scope

Ask what the simulator should *produce*, not how it will be *evaluated*. The
spec describes required behavior, not a validation pipeline.

### Research yourself — safe to look up and propose

- **Codebase structure** — what paths exist, what state is tracked, which
  decisions are sensitive to the feature, which traits and registries apply
- **Artifact schemas** — open the real files the user named and document what
  they actually contain

Do not pre-verify model architecture against upstream sources. Record the model
name; authoritative config verification is `sim-dev`'s job at implementation
time. Doing it here hardcodes values that may drift and removes the
implementation agent's reason to read the real config.

### Never touch

Validation data, evaluation scripts, answer keys, and hidden evaluator
implementations. Encoding their content into a spec makes the evaluation
meaningless.

### Config values are the dangerous middle ground

Existing scenario configs may target different hardware, be outdated, or encode
another experiment's assumptions. **Code structure is safe to reference; config
values require user authority.** If the user names a config, use it but confirm
the critical values. If the user says nothing about hardware, ask — do not fill
the gap from whatever config happens to exist.

## Deciding versus asking

**Stop and ask** when a wrong choice changes what the simulator does or
produces: metric definitions, output semantics, config values, scope
boundaries. Recording a "conservative assumption" for these is not acceptable —
resolving them is the entire point of this skill.

**Decide yourself** for implementation details the user will not care about:
edge-case boundary behavior, format minutiae, internal bookkeeping granularity.
Note the choice in the spec as an explicit assumption so the agent knows it was
deliberate.

### How to ask

Present 2–4 concrete options with a recommendation rather than an open-ended
question, so the user can reply with a letter instead of a paragraph.

> I need to decide the export format.
>
> ⭐ **(A) `Long format`** — *recommended*
> One row per `(time, instance)` sample. Handles multiple instances without a
> schema change.
>
> **(B) `Wide format`**
> One row per timestamp, one column per instance. Compact, harder to extend.
>
> Pick one, or describe something different.

Batch related decisions into one message. Always offer an escape hatch. **Block
all other work while waiting** — do not keep researching or drafting in
parallel, or your options scroll off the user's screen before they answer.

## Spec sections

### 1. Purpose and scope

One sentence stating the goal, explicit non-goals, and the realism boundary.

**Bad:** "Implement caching support."
**Good:** "Implement cache-aware *memory modeling*. The goal is
decision-relevant memory state, not allocator-level byte accuracy. Kernel
throughput effects are out of scope."

### 2. Mechanism background

Give the agent a mental model, not just rules — an agent that understands the
mechanism handles edge cases the spec did not anticipate.

- Summarize the algorithm or system behavior self-contained, so the agent never
  needs the original paper
- State invariants as properties, not code
- Describe each phase with its inputs, outputs, and relation to its neighbors
- Define terminology here so later sections can use it unambiguously

**⚠️ A worked computation trace is mandatory.** Trace one iteration end to end
with real numbers: tokens in, what computes, tokens out, and the resulting
values for every axis the compute model consumes. Use a concrete scenario and
substitute actual values — leave nothing as `K` or `N`. Abstract descriptions
hide off-by-one and off-by-factor errors that silently produce wrong results.

**⚠️ Then cross-check the trace.** For each pair of quantities in a step, decide
whether they are independent or derived from the same operation. Quantities from
the same operation must agree. **If two dependent quantities differ, name the
concrete mechanism that makes them differ — if you cannot, the trace is wrong.**
Do not rationalize a discrepancy you cannot explain physically; that is the
failure mode this check exists to catch.

Skip this section only for pure configuration changes.

### 3. Target system details

Hardware, serving config, model name, and workload location — **all from the
user**, not adopted from existing configs. If the user says "same as scenario
X", confirm those values before adopting them.

### 4. Expected simulator output

The metric, its standalone definition, its granularity, its output channel, and
the mapping if internal state and exported metric differ.

**Bad:** "Export stats that support later validation."
**Good:** "Export per-iteration cache memory usage in bytes, counting only
blocks with at least one live reference, as a time series rather than an
end-of-run aggregate."

### 5. Required mechanism behavior

Frame requirements as observable, falsifiable properties — what must be true,
not how to code it. Cover state evolution, integration points with existing
decisions, and cross-subsystem consistency.

Three levels of detail, handled differently:

| Level | In spec? | Example |
|---|---|---|
| Target mechanism | ✅ In detail | How the modeled system actually behaves, step by step |
| Navigation hints | ✅ Briefly, labeled | "The relevant cache lives near the scheduler" |
| Code-level prescriptions | ❌ Never | Specific enums, API signatures, TOML fields, file-by-file change lists |

Labeling a code-level prescription "suggested" does not help — the agent treats
any specific path or signature as a requirement. If you are writing API shapes
or edit lists, you have crossed from spec into implementation plan. The
implementation agent is an expert coder and understands the codebase more
deeply than you do; give it the mechanism and correct inputs, and it will design
the structure itself.

When a feature depends on external statistics, say what conditioning variables
the data contains so the agent uses that structure rather than collapsing it to
one global average. Do not invent structure the data does not have.

Include a **"good enough" boundary**: the minimum behavioral properties that
constitute completion, and a short "not required" list. If a suitable
abstraction already exists, say "use it".

### 6. Forbidden implementations — task-specific only

Shortcuts the agent might plausibly try that are wrong *for this feature's*
data, scope, or semantics, each with the reason it is wrong here. Generic
anti-patterns belong to `sim-dev`. **Zero items is fine**; most tasks have none
to two.

**Good:** "Do not export only an end-of-run aggregate — this task's metric is
defined as a per-iteration time series."

### 7. Artifacts and schema

For each input and output: path, format, key fields, and their real semantics,
verified against the actual files. Note legacy quirks. Sanity-check numeric
summary fields — if a field's plain-English name implies values its data
contradicts, the name is probably a legacy label.

Include schema facts only. Do not restate what another section already says.

### 8. Self-validation

A checklist of falsifiable properties with expected outcomes, covering
correctness, sensitivity, cross-subsystem consistency, regression, and
observability. State *what* must be verified, not in what order or how many
iterations.

### 9. Deliverables

Working implementation; a reproducible scenario showing the feature has real
behavioral effect; a self-validation report; brief design notes.

## Discipline

**Every feature must be general.** The task's specific parameter values are
inputs to validate against, not the feature's scope. If a config parameter
changes to another valid value, the simulator must handle it with no code
change. State this explicitly in the spec, and do not write validation criteria
that only hold for the task's particular values.

**Qualitative expectations, not derived numbers.** If the inputs are correctly
specified, the agent can compute the numbers. Pre-computed values invite
hardcoding.

| Instead of | Write |
|---|---|
| A step-by-step byte formula with a final figure | "Halving the element width should roughly halve per-token bytes" |
| "Capacity = 56853 blocks" | "The narrower format should admit more requests at the same memory budget" |
| A prescribed metadata layout | "Per-block overhead must be documented, parameterized, and nonzero" |

**No redundancy.** Each fact appears in exactly one section; others reference it.

## Before handing off

- [ ] Worked computation trace is present, with real numbers and no symbols.
- [ ] Trace was cross-checked; every dependent-quantity difference is explained.
- [ ] Requirements hold for any valid configuration, stated explicitly.
- [ ] Hardware, serving config, and workload came from the user.
- [ ] Metric name, definition, granularity, and format are concrete.
- [ ] Semantics are self-contained — no delegation to external source links.
- [ ] No pre-computed numbers and no code-level prescriptions.
- [ ] Forbidden items are task-specific; generic ones were left to `sim-dev`.
- [ ] Schemas were verified against real files; no validation data was read.
- [ ] Each fact appears once, and section numbers are unique.
