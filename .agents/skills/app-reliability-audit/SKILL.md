---
name: app-reliability-audit
description: >
  Use when asked to audit a codebase for reliability, resilience, or production-readiness issues.
  Triggers: "audit my codebase", "find reliability issues", "review for production", "check for
  race conditions / timeouts / N+1 queries", "SRE review", "find vulnerabilities", "trace user
  flows". Also covers performance: "find performance bottlenecks", "why is this slow", "review our
  query / access patterns", "check algorithmic complexity", "why does this re-render", and dead code: "find unused exports",
  "what code is dead", "do we have circular dependencies". Outputs three artifacts:
  `audit_backlog.md`, `audit_traces.md`, and `audit_progress.md`. NEVER executes code changes.
  Do NOT use for security pen-testing, CVE scanning, static type-checking, or profiler / benchmark
  runs (this skill reads code, it does not measure).
---

# App Reliability & Resilience Audit Skill

## Core Philosophy
Trace real user flows end-to-end. Production failures are cascades of small gaps — a missing timeout, a swallowed exception, a schema mismatch — not single catastrophic bugs. Avoid alarm fatigue (flagging every TODO as HIGH) and false confidence (stopping at the controller layer).

Slowness is a reliability failure on a delay. An `O(n^2)` app-side join and an N+1 access pattern are outages waiting for enough rows — audit a flow's algorithm and access shape with the same rigor as its error handling. But a perf finding without a realistic `n` is noise: see the **Performance Gate**.

The audit runs **one flow at a time** — or one batch of independent flows when fanned out to sub-agents (see **Delegating to Sub-Agents**) — and **checkpoints after each flow or batch**. No fixed flow/file cap — it proceeds until the flow queue is exhausted or you pause. State lives entirely in the two artifact files, so a fresh session continues from where the last left off.

The audit supports two modes: **checkpoint mode** (pauses after each flow, waits for `continue`) and **continuous mode** (auto-proceeds without pausing). Reply `continuous` at any checkpoint to switch, or `checkpoint` to switch back.

## Output Economy
Write for a reader who scans. Both artifact files and chat updates use dense fragments, not prose.
- Findings: state trigger → failure → fix in the fewest unambiguous words; no lead-in sentences.
- Code in a finding: only the lines that show the flaw (≤10), never a whole function.
- Log/trace rows: one line per event; never restate context already on the page.
- Chat at a checkpoint: 2–3 lines — done / next / how to continue. Don't paste the artifact back.
- Fill a template field or omit it; don't write "N/A because…".

---

## What Counts as a Flow
A flow **must** start at an entry point (route/UI event/CLI), pass through service/logic layers, and **end at a storage or external service boundary**. Tracing half a flow is an audit failure.

---

## Phase 0 — Learn the Project First (Mandatory)

### 0-A. Build the Structural Map
Answer by reading landmark files only:
1. **Project type?** — `package.json`, `pyproject.toml`, etc. Note frameworks, ORMs, HTTP clients, AI SDKs.
2. **Service boundaries?** — `docker-compose.yml`, `.env.example`, `config/`. List every external service.
3. **Entry points?** — routing files first (`routes`, `router`, `app`, `server`, `main`).
4. **Data models?** — `*.prisma`, `models.py`, `*.schema.ts`, `types.ts`. Ground truth.
5. **File count?** — `git ls-files --cached --others --exclude-standard | wc -l` or `(git ls-files --cached --others --exclude-standard).Count`. (IMPORTANT: scope the listing to required folders only)

Write into **Project Profile**.

### 0-A2. Establish the Scale Baseline
Perf findings need an `n`. Collect once, cheaply, from what the repo already states:
- **Row volumes** — which tables grow unbounded (events, logs, submissions) vs. fixed (roles, statuses). Sources: migrations, seed files, retention jobs.
- **Declared indexes** per table, and the column each one leads with.
- **Caches and pools** — cache layers, TTLs, DB pool size, queue concurrency.
- **Hot paths** — entry points hit per session (auth, list, dashboard, poll) vs. once a day.

No basis in the repo → ask the user for real volumes, or record `unknown`. Never invent a number. Write into **Project Profile** as `Scale baseline`.

### 0-A3. Harvest Static Signals (dead code, duplication, cycles)
One repo-wide pass, before tracing. This finds what flow-tracing structurally cannot: code that **no flow reaches**. Read-only — it inspects, it never edits.

```bash
npx fallow                                           # full pipeline: dead code + duplication + health
npx fallow audit                                     # scoped to what a PR changed
npx fallow audit --format json --quiet 2>/dev/null   # machine-readable, for agents and scripts
```

Use the full pipeline for a whole-repo audit; use `audit` when the review is scoped to a diff. `npm install --save-dev fallow` if the repo vendors its own tooling, otherwise zero-install `npx` is fine. Save raw output under the artifacts folder (`.artifacts/fallow.json`).

**Exit codes:** `0` and `1` both mean the run **succeeded** (`1` = findings exist). Only `2` is a real error, reported as a JSON envelope on stdout. Never treat `1` as a failure.

Record the headline counts verbatim in **Static Signals**:
```
Unused files 44 · unused exports 111 · unused types 41 · unused class members 5 · circular deps 1
```

Non-JS/TS repo, tool absent, or exit `2` → record `Static signals: unavailable ([reason])` and continue. The audit never blocks on a tool.

#### Static signals are leads, not findings
A count is not a finding, and no list is ever copied into the Findings Log wholesale. Before promoting an entry, rule out the standard false positives — grep for the symbol first:
- **Dynamic reference** — reached by string or glob (`require(path)`, `import()`, route auto-registration, DI container, job registry)
- **Framework convention** — called by the framework, not by your code (route module, migration, seed, worker, `*.config.*`, test helper, story)
- **Published surface** — exported via the package `main`/`exports`, or consumed by another workspace in the monorepo
- **Type-only reach** — used solely in a `.d.ts`, a generic constraint, or an inferred contract the tool cannot follow
- **Out of the tool's scope** — excluded path, unresolved tsconfig alias, so the tool never saw the caller

One unverified "delete this" costs more trust than ten correct ones.

#### Which signals earn a finding
Promote only where dead code creates real risk:
- **Diverged stale duplicate** — an unused file/export shadowing a live one; the next reader edits the dead copy (§10, MEDIUM+)
- **Half-removed feature** — dead code paired with a live table, route, env var, or flag still shipped; say which half survives
- **Circular dependency** — a reliability defect, not hygiene: partial module init, `undefined` at import time, TDZ crash, non-deterministic bundle order. Trace the cycle and name the binding that is `undefined` at load; rate by whether it sits on a startup or request path.
- **Dead error handling** — a guard, retry, or fallback nothing can reach: protection you believe you have and do not

Everything else is **one** batched LOW finding carrying the counts and the artifact path. Never write 111 findings.

### 0-B. Learn the Documentation Pattern
Read 2–3 well-documented files to capture: comment style, error message format, naming conventions, logging pattern (structured vs. plain), return conventions (tuples / exceptions / Result types), test coverage. Record in **Project Patterns**. All remediations mirror these exactly.

### 0-C. Calibrate the Quality Baseline
Silently read one complete flow without flagging. Determine: is error handling absent or just inconsistent? TODOs on critical paths? Is defensive coding the norm? A systemic pattern outranks an isolated one on severity.

### 0-D. Build the Flow Backlog
Read the routing layer and all entry points (route files, server entry, scheduled jobs, webhook receivers, queue consumers, CLI commands, event listeners). List every distinct flow in a **Flow Backlog** showing the actual call path:

```
FLOW-001 Create resource     → `POST /api/resources` → `service.create` → `store.save` → DB write
FLOW-002 Get resource        → `GET /api/resources/:id` → `service.getById` → `store.findOne` → DB read
```

Cross-check entry points against 0-A3's unused-files list: an entry point reported unused is either a dead route or a tool false positive — resolve which, in one line, before building the backlog. Do not queue a flow for code nothing can reach.

Order: highest-risk/traffic first. The backlog is the **source of truth** for all known flows. During Phase 2, flows discovered mid-audit (following a call chain reveals an undocumented path) are **appended** to the backlog.

---

## Phase 1 — Initialize `audit_backlog.md`

Create in project root before scanning any source file. The flow queue.

```markdown
# Audit Backlog

| Flow ID | Status | Flow Path |
|---------|--------|-----------|
| FLOW-001 | ⏳ Pending | `entry` → `service` → `store` → boundary |

Status: ⏳ Pending / 🔍 Current / ✅ Done / ⛔ Dead-end
New flows discovered mid-audit → appended as ⏳ Pending.
```

**Mutation Rules:**
| Section | Rule |
|---|---|
| Flow status | Move from ⏳ → 🔍 → ✅ (or ⛔). Never delete entries. |
| Discovered flows | Append as ⏳ Pending. |

---

## Phase 1.5 — Initialize `audit_traces.md`

```markdown
# Reliability Audit — Flow Traces
### 🔍 Trace — FLOW-001: [desc]
| # | File | Function | Action |
|---|---|---|---|
| 1 | `router.py` | `POST /resource` | input validation |
| 2 | `service.py` | `create_resource` | business logic |
| 3 | `store.py` | `save_to_db` | atomic write + error bubbling |

**Simulated:** [1–2 lines: the user flow and the reliability concern tested.]
**Cost:** [N DB · N cache · N HTTP] round trips, [sequential|batched] · dominant complexity `O(?)` on `n` = [what] · payload note (e.g. 40 cols fetched, 3 read).
```

---

## Phase 1.75 — Initialize `audit_progress.md`

```markdown
<!-- FINAL SUMMARY prepended here on completion -->

# Reliability Audit — Progress

## Status
| Field | Value |
|---|---|
| Phase | Phase 0 / Auditing `FLOW-NNN` / ⏸️ Checkpoint / ✅ Queue exhausted |
| Flows done | X / Y |
| Findings | N (🔴 HIGH N · 🟠 MEDIUM N · 🟡 LOW N) |
| Last checkpoint | after `FLOW-NNN` |

## Project Profile
- Language/runtime · Framework · ORM/DB client · External services · Total files · Key risk areas
- Scale baseline: growing tables · declared indexes · caches/TTLs · pool + concurrency limits · hot paths

## Static Signals
- `fallow` run [date] · unused files N · unused exports N · unused types N · unused class members N · circular deps N · raw: `.artifacts/fallow.json`
- Verified / dismissed as false positive: N / N

## Project Patterns
- Error handling · Logging · Naming · Return conventions · Test coverage

## Findings Log
_(Append-only. Findings reference Flow IDs linked from audit_traces.)_
```

### Mutation Rules
| Section | Rule |
|---|---|
| Status | Overwrite on every file read |
| Project Profile / Patterns | Fill once; overwrite with `*(revised)*` if corrected |
| Findings Log | Append only. Add `**Updated:**` line if severity escalated or blast radius changed. |

---

## Phase 2 — Execution Loop

One flow at a time; checkpoint after each. Repeat until the queue is empty or the user pauses.

**2-A. Select next flow** — pick first `⏳ Pending` from `audit_backlog.md`. Mark it `🔍 Current`. In continuous mode, select a batch of 3–5 independent flows and dispatch them per **Delegating to Sub-Agents**.

**2-B. Walk the flow** — follow the real call chain from entry to terminal boundary. At each step, append a row to `audit_traces.md` (file | function | action). This walk is the primary unit of delegation: hand it to a sub-agent with the trace brief and merge what comes back — the orchestrator still writes every row. While walking, tally the flow's access shape — every DB query, cache read, and outbound HTTP call, plus any loop whose bound is user data — so the trace's **Cost** line can be filled at the boundary. A round trip inside a loop is the highest-yield thing this walk catches.

**2-C. Write findings** — when a flaw is found, write it to `audit_progress.md` Findings Log using the Finding Template. Link the finding ID in `audit_traces.md` Action column. If a new flow is discovered mid-audit, append it to `audit_backlog.md` as `⏳ Pending`.

**2-D. Checkpoint** — no fixed budget; checkpoint at natural boundaries so the run is pausable/resumable. Triggers:
```
flow reaches terminal boundary   → CHECKPOINT (flow done)
3 consecutive dead ends          → CHECKPOINT (mark ⛔ Dead-end)
user pause / context long        → CHECKPOINT
flow queue empty                 → CHECKPOINT + Final Summary
```
At every checkpoint: flush both files; mark flow `✅ Done` (or `⛔ Dead-end`) in backlog; set next `⏳ → 🔍`; set Status `⏸️ Checkpoint`; emit checkpoint line. In **checkpoint mode**: stop and wait for user. In **continuous mode**: auto-proceed to next flow (loop back to 2-A).

**Checkpoint line:**
> **Checkpoint — `FLOW-NNN` done.** [N] findings · [Y]/[X] flows. Top: [one line]. Next: `FLOW-NNN`. Reply **continue**, or `continuous` to auto-proceed, or resume from `audit_backlog.md`.

---

## Delegating to Sub-Agents

Tracing a flow is read-only, independent, and has a fixed output shape — the most delegable work in this audit. Fan out the reading; keep the judgment and the pen.

### The one hard rule
**The orchestrator is the sole writer of `audit_backlog.md`, `audit_traces.md`, and `audit_progress.md`.** Sub-agents return structured text and never open an artifact file. Concurrent writers corrupt the ledger, and the ledger is the only thing that makes a paused audit resumable.

### Delegate
| Work | Shape | Model |
|---|---|---|
| Trace one flow end to end | one sub-agent per flow | default |
| "Where is `X` called from?" / "is this guarded elsewhere?" (chain step 5) | one sub-agent, questions batched | cheapest available |
| Verify a `fallow` unused list against the false-positive checklist (0-A3) | one sub-agent for the whole list | cheapest available |
| Locate entry points and routing files in an unfamiliar repo (0-A, 0-D) | one sub-agent | cheapest available |
| Confirm an index / read migrations for the scale baseline (0-A2) | one sub-agent, batched | cheapest available |

### Never delegate
- **0-B and 0-C** — Project Patterns and the quality baseline are the calibration every severity call rests on. Read them yourself or every downstream rating is guesswork.
- **Severity, the Performance Gate, the Reasoning-Before-Flagging chain** — sub-agents propose, the orchestrator rates. An agent that has seen one flow cannot judge *systemic*, does not know what 0-C established as normal for this repo, and will over-rate.
- **Checkpoint decisions, artifact writes, the Final Summary.**

### Flow fan-out
Continuous mode: dispatch 3–5 independent flows at once. Checkpoint mode: one at a time, so each pause maps to one reviewable flow.
1. Mark every dispatched flow `🔍 Current` **before** dispatch.
2. Merge returned traces in **backlog order, not completion order** — artifacts stay deterministic across runs.
3. A sub-agent that fails or returns nothing → set that flow back to `⏳ Pending` with the reason. Never strand a flow at `🔍`.
4. One checkpoint per batch.

Do not fan out flows you are reasoning about *together* — two writers to the same table in a race investigation, or a create/delete pair being checked for symmetry (§11). Cross-flow reasoning needs one context.

### Trace brief
Give each sub-agent what it cannot infer, and demand a fixed return shape. Anything vaguer returns prose you then have to re-read.

```
Trace FLOW-NNN, read-only: `entry` → `service` → `store` → boundary.
Do not edit any file. Do not write artifacts. Report only.

Context:
- Project patterns: [error handling · logging · return conventions — one line each]
- Scale baseline: [table volumes / indexes / hot-path status relevant to this flow]
- Categories to apply: [the numbered list, or the subset that fits this flow]

Return exactly:
1. TRACE ROWS — `| # | file | function | action |`, one per real step, entry to terminal boundary.
2. COST — DB · cache · HTTP round trips, sequential or batched, dominant complexity, `n` and where the number came from.
3. CANDIDATE FINDINGS — each with: file + line range, the trigger input, the observed failure,
   proposed category, and the ≤4 lines of code that show it. Propose a severity, marked PROVISIONAL.
4. UNRESOLVED — what you could not follow (dynamic dispatch, missing file, external service) and the
   last known location. Never guess a path or invent a symbol name.

If the chain leaves the codebase, stop at the boundary and say so.
```

### Trust, then verify
A sub-agent's finding is a claim, not a fact. Before any CRITICAL or HIGH enters the Findings Log, open the cited lines and confirm them yourself — the standard failure is a confident report about code that does not read the way it was described. Demote to a lead and re-check when the cited lines do not exist, the symbol names do not match, or the failure story depends on a call path you cannot see. MEDIUM and LOW can be taken on report; spot-check them.

---

## Reasoning Before Flagging

Before writing any finding, run this chain — it separates a real flaw from a style preference or missing feature.

1. **Intended behavior?** A missing null check is only a flaw if `null` can actually arrive here.
2. **What input breaks it?** Name it concretely ("empty array", "concurrent second request", "504 from the AI API"). Can't name it → not a finding yet; keep reading.
3. **What happens on break?** Throws and propagates cleanly? Swallows and returns corrupt data? Partial write → inconsistent state? The failure mode sets severity, not the pattern.
4. **Right layer to fix?** A missing check in B may be a design gap in A. Flag where the fix is most durable.
5. **Already handled elsewhere?** Middleware, base class, wrapper, DB constraint. Confirm the guard exists before skipping — flagging a guarded issue is noise.
6. **Flaw or choice?** Unbounded query on a ≤50-row table, no timeout on a loopback call, optimistic update with client rollback — if intentional (comment/config/test), downgrade or skip and note the assumption.

Only after all six should you write a finding.

---

## Performance Gate

Applies to **Algo**, **Access**, and **Perf** findings only. A perf finding needs a number, not a smell. State all five before writing one:

1. **Realistic `n`** — items/rows/calls at p50 and at the plausible ceiling, plus where the figure came from (schema, seed data, page-size default, an existing `LIMIT`, the user). No basis → write `n` unknown and cap at LOW.
2. **Complexity now → after** — `O(n^2)` app-side join → `O(n)` via a `Map` index; 1+N round trips → 2. If neither the complexity class nor the round-trip count changes, it is a constant-factor rewrite.
3. **Where the cost lands** — request latency, blocked event loop, DB CPU, memory ceiling, or upstream quota. The landing zone sets severity, not how ugly the code is.
4. **Hot path?** — per request, per rendered row, or per queue message outranks a nightly job at identical complexity.
5. **Bounded by construction?** — fixed enum, config list, `LIMIT 50` already applied upstream. If `n` provably cannot grow, say so and skip or downgrade.

Constant-factor rewrites are LOW at most, and only when they cost nothing in clarity. Never flag a perf issue with a fix you have not sized: "add an index" is not a finding unless you name the column order and the query it serves.

---

## The Eleven Audit Categories

### 1 — Schema & Model Alignment
- **Frontend → API**: field names, casing, required vs. optional
- **Pydantic / TS optionality**: logically required fields (`project_id`, `user_id`) must not be `Optional`
- **API → Service**: destructured fields match the service signature
- **Service → DB**: ORM model matches column names, types, nullability
- **AI response → DB**: validated against schema before persistence
- **API response → Frontend**: DTO field names/types match what the frontend consumes

### 2 — Exception & Error Handling
Every error must bubble, be logged with context, or be explicitly handled. Flag: empty catch / `.catch(() => {})` (CRITICAL on write/payment/auth); unawaited promise-returning calls; errors with no operation/entity/input context; caller getting success after an error was caught and not re-thrown.

### 3 — Timeout & Back-Pressure
Every cross-process call needs an explicit timeout (HTTP, DB on large sets, queue polls, remote file I/O). Also: retries without exponential backoff + jitter; no circuit breaker on high-traffic AI/payment deps; unbounded loops over DB records with no chunking/pagination.

### 4 — Database Safety
- **N+1**: per-iteration DB call where a bulk query serves the same purpose
- **Missing transactions**: 2+ writes that must succeed/fail together, unwrapped
- **Connection per request**: DB client built inside a per-request handler
- **Missing indexes**: filter/sort fields on large tables absent from the schema

### 5 — Concurrency & Race Conditions
Check-then-act without atomicity (fix: `upsert` + unique constraint); missing idempotency keys on payment/order/subscription endpoints; shared mutable state without sync in a multi-process/thread context; optimistic concurrency without version/timestamp check before write.

### 6 — Rate Limiting & Back-Pressure
Public write/expensive endpoints without rate limiting; background jobs without a concurrency cap; outbound calls to rate-limited upstreams without a 429 handler / token bucket; webhook receivers without signature verification.

### 7 — Algorithmic Complexity & Data Structures
The work done per item, in application code. Rate by complexity class × realistic `n` (Performance Gate), never by aesthetics.
- **Nested iteration over the same data**: `O(n^2)` app-side join, dedupe, or lookup where indexing one side into a `Map`/`Set` makes it `O(n)` — the most common real offender
- **Linear scan inside a loop**: `.find` / `.includes` / `.indexOf` / `x in list` against a growing array per iteration → build the keyed index once, outside the loop
- **Sorting for the wrong reason**: sorting inside a loop; a full `O(n log n)` sort to take min/max/top-k; re-sorting data the source could have returned ordered
- **Wrong container for the access**: array used for membership or keyed lookup; `shift`/`unshift`/`splice(0,…)` as a queue (`O(n)` per op); ordered iteration over a hash, re-sorted on every call
- **Quadratic accumulation**: string `+=` in a loop, `[...acc, x]` or `{...acc}` spread inside a `reduce`, repeated `concat` — allocate once, push, join once
- **Redundant deep work**: `JSON.parse(JSON.stringify(x))`, deep clone, or full re-serialization per item where a shallow copy or a reference serves
- **Recursion hazards**: overlapping subproblems with no memoization; recursion depth tied to user input (stack overflow, not slowness)
- **Whole-set work for a partial answer**: load-then-filter, map-then-take-one, count-by-materializing — push the predicate, the limit, and the aggregate down to the source
- **Loop-invariant derivation**: value recomputed per item from data that is constant across the loop — hoist it

### 8 — Data Access Patterns
How data is *reached* across a whole flow. Judge the flow's access shape, not one call site — a single query is fine; the same query 200 times is the outage.
- **Round-trip count**: total DB + cache + HTTP calls for one request. Sequential *dependent* round trips are the latency floor. Collapse chains into one query, one batch, or one join.
- **Batching absent**: per-item fetch where the source takes a set (`WHERE id = ANY($1)`, `mget`, a bulk endpoint). N+1 generalized to cache and HTTP, not just the DB (§4 covers the DB case).
- **Index/predicate mismatch**: filter, join, or sort columns with no matching index; wrong leading column in a composite index; a predicate that defeats the index (`LOWER(email) = …`, leading-wildcard `LIKE`, cast on the column side).
- **Pagination strategy**: `OFFSET` deep-paging on a growing table (cost rises with the offset) → keyset/cursor. Also: no `LIMIT` on a user-supplied filter, no maximum page size, unindexed `COUNT(*)` for every page.
- **Projection**: rows, columns, or relations fetched but never read; a relation eager-loaded for one conditional branch.
- **Read-modify-write round trips**: fetch → mutate in app → write, where one atomic `UPDATE`/upsert does it. Also a race (§5) — flag once, cite both.
- **Cache correctness**: key omits something the value depends on (tenant, user, locale, schema version); no TTL or no invalidation on the write path; stampede on expiry with no lock or jitter; caching a per-process-stable value per request, or the reverse.
- **Client-side waterfalls**: dependent fetches in sequential effects; a fetch keyed on an unstable dep so it refires every render; one request per rendered row.
- **Write amplification**: one logical action fanning out to many single-row writes where a bulk insert inside one transaction serves.
- **Hot-path scan of cold storage**: full table scan, unindexed `COUNT(*)`, or object-store listing on a per-request path.

### 9 — Runtime Performance & Efficiency
Cost that is neither the algorithm nor the access shape. Flag only where it degrades under *realistic* load.
- **Redundant computation**: same value derived >once per request with no memoization
- **Sequential I/O**: `await a(); await b();` where `a`,`b` are independent and parallelizable
- **Hot-path re-init**: config parse, regex compile, schema object, SDK client built per request (belongs at module load)
- **Unbounded memory**: append to an in-memory structure with no cap/flush (streams, background jobs)
- **Blocking I/O on async thread**: sync file reads, `JSON.parse` of huge payloads, CPU-heavy transforms on the event loop
- **Payload weight**: response size driven by unbounded nesting or a large blob column on a list endpoint

**Render path** (React and other component UIs). Apply the Performance Gate with `n` = list length × render frequency:
- **Re-render cascade**: state held above the components that read it — one keystroke repaints a page. Colocate the state or split the component.
- **Unstable props and deps**: inline object / array / function literals passed to memoized children; `useMemo`/`useCallback` dependencies whose identity changes every render. Memo present, benefit zero.
- **Context thrash**: one provider carrying unrelated values, so every consumer re-renders when any field changes — split by change frequency.
- **Index keys on reorderable lists**: remounts, lost input state, wrong DOM reuse (a correctness bug that looks like a perf bug).
- **Unvirtualized long lists**: 1000+ rows mounted with no windowing or pagination.
- **Work in render**: sort / filter / derive over a large array on every render (§7), or a value duplicated into `useState` + `useEffect` where deriving it during render is correct.
- **Layout thrash**: reading `getBoundingClientRect` / `offsetWidth` then writing style in the same frame; animating properties that trigger layout instead of `transform` / `opacity`.
- **Eager bundle weight**: a heavy dependency imported at a route root, a barrel import pulling a whole library, no code splitting on rarely visited routes.


### 10 — Duplication, Dead Code & Abstraction Flaws
Flag only when duplication or dead surface creates real divergence risk.
- **Diverged duplicates**: same logic in 2+ places, one updated and the other not (the only immediate reliability risk)
- **Inline reimplementation** of an existing utility (cite its location)
- **Copy-pasted validation** without a shared schema — one will drift
- **Parallel type hierarchies** (DB model / DTO / API type) kept in sync by hand, no mapper
- **Repeated try/catch boilerplate** where a wrapper/middleware would centralize
- **Unreachable surface**: files, exports, types, or class members no flow reaches — confirm against 0-A3's false-positive list, then batch (one LOW finding) unless it shadows live code or strands half a removed feature
- **Circular imports**: `A → B → A` at module load — name the binding that is `undefined` during init and the path (startup vs. request) that hits it

### 11 — Logic Flaws & Behavioral Correctness
Hardest to find, most damaging — they look like working code.
- **Boundary conditions**: off-by-one pagination, wrong operator (`>` vs `>=`), date fence-posts — trace the math
- **Assumption violations**: assumes sorted list / non-empty / UTC / lowercase without upstream guarantee — read producer + consumer together
- **Silent default substitution**: `|| 0`, `?? ''`, `or []` masking an unexpected missing value with no log
- **State-machine violations**: any caller can write any state; no transition guard or DB constraint
- **Wrong aggregation scope**: missing `WHERE`, row-multiplying join, or filter applied after aggregation
- **Auth after fetch**: ownership filtered in app code after the query — predicate must be *in* the query
- **Asymmetric create/delete**: create writes N stores, delete cleans up fewer — trace both

---

## Severity Rubric

| Severity | Condition |
|---|---|
| **CRITICAL** | Data loss, silent corruption, full outage, or auth boundary crossed. Wrong outcome every time, regardless of load. |
| **HIGH** | Significant user-facing failure / data inconsistency under realistic load or normal retries. Needs a deploy or manual DB fix to recover. |
| **MEDIUM** | Degrades gracefully but causes visible errors, measurable perf regression, or latent divergence that will surface later. |
| **LOW** | Code-quality / abstraction / optimization gap, no current user impact. |

**Perf severity**: rate by the landing zone at realistic `n`. Times out, exhausts the pool, or blocks the loop under normal load → HIGH. Measurable but survivable regression → MEDIUM. Structural gap with `n` provably bounded small → LOW. A worse *complexity class* on a growing table is HIGH even when it is fast today — name the `n` at which it breaks.

**Dead-code severity**: LOW by default — unreachable code has no user impact. Escalate only for a stale duplicate of live code (MEDIUM), a half-removed feature whose live half still runs (MEDIUM), or a circular dependency on a startup/request path (MEDIUM–HIGH, rated by what is `undefined` when it fires). Volume never escalates dead code; 111 unused exports is still one LOW finding.

**Systemic upgrade**: same flaw in 3+ files → escalate one level; state reason + files.
**Don't escalate** a zero-impact LOW just for frequency. Frequency amplifies impact-based severity, not zero-impact severity.

---

## Finding Template

```markdown
### [CRITICAL|HIGH|MEDIUM|LOW] — <specific title>

| | |
|---|---|
| Issue | `ISSUE-NNN` |
| Flow | `FLOW-NNN` |
| File | `path` lines X–Y |
| Category | Schema / Exception / Timeout / DB / Race / RateLimit / Algo / Access / Perf / Duplication / DeadCode / Logic |
| Systemic | Yes (`fileA`,`fileB`) / No |

**Flaw** — trigger input + what breaks. Code ≤10 lines.
**Impact** — what fails, for whom, how visibly, auto-recover or manual.
**Fix** — before/after using the project's own error class, logger, naming. Note any migration/schema/config change.

_Algo / Access / Perf findings add one more line; omit it for every other category:_
**Cost** — `n` = [value + source] · now `O(?)` / N round trips → after `O(?)` / N · lands on [latency|DB CPU|event loop|memory|quota].
```

---

## Flow Trace Template

```markdown
### 🔍 Trace — FLOW-NNN: [desc]
| # | File | Function | Action |
|---|---|---|---|
| 1 | `router.py` | `POST /resource` | input validation |
| 2 | `service.py` | `create_resource` | business logic |
| 3 | `store.py` | `save_to_db` | atomic write + error bubbling |

**Simulated:** [1–2 lines: the user flow and the reliability concern tested.]
**Cost:** [N DB · N cache · N HTTP] round trips, [sequential|batched] · dominant complexity `O(?)` on `n` = [what] · payload note (e.g. 40 cols fetched, 3 read).
```

---

## Final Summary Block

Prepend to top of `audit_progress.md` when the queue is exhausted or the user ends at a checkpoint.

```markdown
<!-- ════════════════════════════════════════════════════════ -->
## ⛔ FINAL SUMMARY

Checkpoint reason: Queue exhausted / User ended
Flows done: Y / X

### Health: 🔴 Critical / 🟠 High Risk / 🟡 Moderate / 🟢 Healthy
_(one paragraph: worst finding, most pervasive pattern, highest-density layer, isolated vs. systemic)_

### Counts
🚨 CRITICAL N · 🔴 HIGH N · 🟠 MEDIUM N · 🟡 LOW N · **Total N**

### Top 3 Fixes
1. `[file:lines]` — issue → fix
2. `[file:lines]` — issue → fix
3. `[file:lines]` — issue → fix

### Top 3 Perf Wins
_(Omit if no Algo/Access/Perf finding cleared the Performance Gate.)_
1. `[file:lines]` — `O(?)` / N round trips → target, at `n` = [value]
2. `[file:lines]` — …
3. `[file:lines]` — …
<!-- ════════════════════════════════════════════════════════ -->
```

Then send:
> **Audit complete — queue exhausted.** [N] findings · [Y]/[X] flows. Most urgent: [one line]. Unaudited flows in `audit_backlog.md`.

---

## Resuming
State lives in the artifact files. Read them in this order and stop as soon as you have the pointer — resuming should cost a few hundred lines, not the whole audit.

1. **`audit_progress.md` first.** The Status block *is* the pointer: phase, flows done X/Y, findings count, last checkpoint. Re-read Project Profile, Scale baseline, and Project Patterns while you are here — every severity call rests on that calibration, and it is short.
2. **Current flow?** A flow marked `🔍 Current` → resume it at 2-B. More than one at `🔍` means a fan-out batch died mid-flight: reset those to `⏳ Pending` and re-dispatch.
3. **No current flow → `audit_backlog.md`.** Take the first `⏳ Pending`; skip `✅ Done` and `⛔ Dead-end`. Nothing pending → the queue is exhausted; write the Final Summary.
4. **`audit_traces.md` on demand only, and only by targeted search.** It is the largest artifact and grows without bound. Never read it whole, and never read it to get oriented — steps 1–3 already did that. Grep the one section you need:

```bash
grep -n "Trace — FLOW-042" .artifacts/audit_traces.md   # locate the section
sed -n '120,150p' .artifacts/audit_traces.md              # read only that block
```

Open it when a decision depends on what an earlier flow established: whether this file was already walked, where a shared service terminated, what Cost line the flow hitting this same table recorded.
5. **Findings, same rule.** Grep the Findings Log for the `ISSUE-NNN` or the file path in question. Do not re-read the log end to end.

Never re-derive Phase 0. A filled Project Profile and Project Patterns are authoritative — re-running 0-A/0-B burns a context and risks a second, conflicting baseline.

---

## Project-Level Conventions

### Plans & Artifacts Folder
Place all plan documents and audit artifacts inside a dedicated folder at the project root that is gitignored. The recommended names are `.artifacts/` or `.scratch/` (add to `.gitignore`).

### Sub-Agent Usage
During an audit, **Delegating to Sub-Agents** governs — it is more specific than this convention. Generally: for broad exploration tasks (finding files, understanding file patterns, searching code), use a sub-agent with the minimum-cost model available to avoid context rot and preserve budget for the main task. For large repetitive refactors (e.g., renaming a function across 20+ files, updating the same pattern in many modules), delegate to a sub-agent with clear per-file instructions and a checkpoint after every batch. Verify each batch's output before starting the next.
```