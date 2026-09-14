---
name: app-reliability-fix
description: >
  Use after a reliability audit to implement fixes. Triggers: "implement the audit fixes",
  "fix the issues found", "apply the remediations", "start fixing", "fix the reliability issues".
  Reads findings from audit_progress.md, builds a plan, gets approval, then executes one fix at
  a time with a checkpoint after every change. Do NOT edit files before plan approval. Do NOT
  run a new audit — that is the reliability-audit skill's job.
depends_on: app-reliability-audit
---

# App Reliability Fix Skill

## Core Philosophy
Plan first, get approval, then execute one fix at a time. Each fix has dependencies, a blast radius, and regression risk. The user knows things the audit doesn't — an approach may clash with a library decision, or a pattern may be intentional policy. Surface that before touching any file.

Runs **one fix at a time** with a **checkpoint after every change**. Supports **checkpoint mode** (pauses after each fix, waits for `continue`) and **continuous mode** (auto-proceeds without pausing). Reply `continuous` at any checkpoint to switch modes. No batch mode. State lives in `fix_plan.md`, so a fresh session resumes from the plan alone — without losing the approval gate or re-applying a done fix.

## Output Economy
Write for a reader who scans. Plan and chat updates use dense fragments, not prose.
- Approach Details: one tight line per aspect (what changes / error class / what stays / risk).
- Execution Log entries: bullets, not paragraphs.
- Chat at a checkpoint: 3 lines — done / next + planned approach / prompt. Don't paste the plan back.
- Announce blocks (3-A): the 4 fixed lines, nothing more.

---

## Artifacts
- **`fix_plan.md`** — single source of truth: Project Patterns, Fix Inventory (per-fix status), Approach Details, Execution Order, append-only Execution Log. Everything a resuming session needs is here.
- **`audit_progress.md`** — read-only input; only annotated with `✅ FIXED` / `⏭️ DEFERRED` prefixes.
- **`walkthrough.md`** — final summary, created once all fixes complete.

> No separate task tracker. Per-fix state = the Fix Inventory **Status** column; next fix = the first `Pending` item in Execution Order.

---

## Pre-Flight Check
1. **Resume?** If `fix_plan.md` already exists → go to **Resuming** below, not Phase 0.
2. **Fresh start?** Read `audit_progress.md`. If it doesn't exist or has no Audit Log entries, stop:
   > "No audit findings found. Run the reliability-audit skill first, then return here."

---

## The Five Phases

| Phase | Name | Hard stop? |
|---|---|---|
| 0 | Ingest & understand | — |
| 1 | Build `fix_plan.md` | — |
| 2 | Present for review | ⛔ no file touched until explicit approval |
| 3 | Execute → checkpoint → repeat | ⛔ stops after every fix (continuous mode skips the wait) |
| 4 | Finalize `walkthrough.md` | — |

---

## Phase 0 — Ingest & Understand

**0-A. Read audit findings.** Per finding, extract: severity, category, file+lines, systemic flag, any `**Updated:**` blocks. Copy **Project Patterns** verbatim — every fix matches it.

**0-B. Targeted read for planning — not whole files.** Per finding, read only what's needed to write a correct approach: the flagged line range + a small surrounding window, the enclosing function signature, and the file's imports. Don't slurp entire files, and don't trust the audit snippet alone (may be stale/clipped). Widen only when the approach genuinely needs whole-module context (e.g. extracting a shared utility across many sites). Note what the fix must preserve (signatures, tests). The authoritative full read happens just-in-time at execution (3-B), on the freshest file, only for fixes that run.

**0-C. Map fix dependencies.** Shared infra (DB singleton, error class) before its users · schema alignment before logic · transactions before idempotency · systemic fixes as a group (no partial state).

**0-D. Draft an approach per fix.** Decide: exact lines, which project-pattern constructs (error class, logger, txn API), new imports, what could go wrong. These become **Approach Details** — the thing the user reviews.

---

## Phase 1 — Build `fix_plan.md`

```markdown
# Fix Implementation Plan
> Status: Draft — Awaiting Review · Source: `audit_progress.md` · Updated: [what/why]

## Project Patterns (from audit)
- Error handling · Logging · Naming · Return conventions · Doc style

## Fix Inventory
| ID | Issue(s) | Sev | Title | File(s) | Category | Depends on | Status |
|---|---|---|---|---|---|---|---|
| FIX-001 | ISSUE-012 | 🚨 | Missing txn on order create | `orderService.ts:88` | DB | FIX-004 | Pending |
_(Status: Pending / ✅ Done / ⚠️ Partial / ⏭️ Deferred)_

## Approach Details
_(one subsection per fix — the review surface)_
### FIX-001 — [title]
- File: `path` X–Y
- Approach: what changes, which constructs, which lines
- Error handling: exact class, log fields, level
- Unchanged: public signature, unrelated logic
- Risk: what could go wrong + how the approach handles it

## Execution Order
_(deps → severity → blast radius; explain non-obvious ordering)_
1. FIX-004 — reason
2. FIX-002 — reason

## Deferred / Excluded
_(empty until review)_

## Execution Log
_(append-only)_

## Verification Plan
- Run existing tests per changed file.
- Manually trigger the affected flow; confirm the previously silent error is now handled.
```

Plan ends Phase 1 with **Status: Draft — Awaiting Review**.

---

## Phase 2 — Present for Review (Hard Stop)

Present `fix_plan.md`. Your message:
1. **Headline** — finding counts by severity, plain English.
2. **Order rationale** — one line for the first two ordering decisions.
3. **Point at Approach Details:**
   > "Approach Details says exactly what each fix does — which API, error class, log fields, lines. Read each before I write a line of code. Wrong library, wrong error class, different architectural preference? Catch it now."
4. **Gating questions:** Any to skip? defer? change approach?
5. **Prompt:** *"Reply 'approved' to begin, or share feedback and I'll revise first."*

**On approval:** set plan header **Status → ✅ Approved**. This is the only transition that unlocks source edits — here or on any future resumed session.

### Handling Feedback — one operation per item
| User says | Operation |
|---|---|
| "Skip FIX-X, intentional" | → Deferred/Excluded w/ reason |
| "Defer FIX-X till after release" | → Deferred/Excluded w/ reason |
| "Do FIX-X last" | Reorder Execution Order; note reason |
| "Use X not Y" | Update Approach Details; mark *(user-revised)* |
| "Error class is `ServiceError` not `AppError`" | Update Project Patterns *(user-corrected)* + every Approach Details referencing the old name |
| "Add a fix for X" | Add to Inventory + Approach Details + Execution Order |

Set Status `Revised — Awaiting Approval`; list changes in Updated; re-present. Repeat until explicit approval. **Silence ≠ approval.**

---

## Phase 3 — Execute, Checkpoint, Repeat

Two modes: **checkpoint mode** (pauses after each fix) and **continuous mode** (auto-proceeds without pausing). Default is checkpoint mode. Reply `continuous` at any checkpoint to switch, or `checkpoint` to switch back.

### 3-A. Announce (before touching files)
```
Applying FIX-002 — [title] (ISSUE-NNN)
File: path X–Y
Approach: [one line, as approved]
(no other files touched)
```

### 3-B. Apply
First **re-read the target region fresh** (named lines + enough context to edit safely) so the patch is against current state, not the Phase-0 snapshot — matters most across sessions. If the file drifted from what Approach Details assumed, pause and flag before editing.
Then edit only this fix's files. Must: match Approach Details + Project Patterns exactly · not change public interface unless the fix says so · not add deps without announcing in 3-A.

### 3-C. Update artifacts (after every fix)
1. Append to Execution Log:
```markdown
### ✅ FIX-NNN — [title] (ISSUE-NNN)
- Files: `path`
- Done: [lines changed]
- Unchanged: [signature, unrelated logic]
- Tests: [pass/fail/gap]
- Deviation: None / [what + why]
```
2. Set Fix Inventory Status → `✅ Done` (that + Execution Order is the resume pointer).
3. In `audit_progress.md`, prepend `✅ FIXED — ` to the finding title. For deferrals: prepend `⏭️ DEFERRED — `, replace Remediation with a **Deferred Reason**, add the legend once:
```markdown
## Audit Legend
- `✅ FIXED` — resolved · `⏭️ DEFERRED` — acknowledged, out of scope this run
```

### 3-D. Checkpoint (after every single fix)
```
Checkpoint — FIX-002 done. Next: FIX-001 — [title] (`file:lines`), approach: [one line].
Reply 'continue' / 'pause' / 'continuous' — or feedback to adjust before the next fix.
```
In **checkpoint mode**: stop. Do not proceed until the user responds. In **continuous mode**: auto-proceed to next fix (loop back to 3-A). The next `Pending` fix is the resume point.

### 3-E. Checkpoint responses
| User says | Action |
|---|---|
| continue / yes | apply next → 3-A |
| continuous | switch to continuous mode; auto-proceed to next fix (3-A) |
| pause / stop | write Pause Summary (3-G); Status → ⏸️ Paused; stop |
| skip this one | → Deferred/Excluded; announce new next; wait |
| change approach to X | update Approach Details *(user-revised)*; re-announce (3-A); wait |
| add this fix first | add to Inventory + Approach Details + Execution Order; announce; wait |

### 3-F. Finalize
All fixes done → Status → `✅ Complete`; create `walkthrough.md`: changes made, tests run, final state, follow-up gaps noticed.

### 3-G. Pause Summary
Prepend to `fix_plan.md`:
```markdown
<!-- ════════════════════════════════════════════════════════ -->
## 📋 Implementation Summary — Paused / Complete
Fixes done: N / M

### Applied
| ID | Title | File(s) | Issue(s) | Deviation? |
### Remaining
| ID | Sev | Title | Issue(s) | Reason not applied |
### Deferred
| ID | Title | Issue(s) | Reason |
### Follow-up gaps (noticed, not in original audit)
- `file` — [what]

### Resume
New session → say "resume the reliability fixes". Plan Status + Fix Inventory show the state;
work starts at the first `Pending` fix. (Never edit source unless Status = Approved.)
<!-- ════════════════════════════════════════════════════════ -->
```

---

## Resuming
State lives in `fix_plan.md`. Read in this order and stop at the pointer.

1. **`fix_plan.md` first.** The header **Status** is the gate: `Draft`/`Revised` → re-present for review, **never edit source on an unapproved plan**. `Complete` → offer the walkthrough. Take Project Patterns from the plan, not from a fresh read of the audit.
2. **Approved → Fix Inventory + Execution Order.** Resume at the first `Pending` in Execution Order via 3-A; skip anything `✅ Done`. The Status column outranks whatever you infer from the source tree.
3. **Execution Log — last 2–3 entries only.** Enough to see what the previous session actually changed and whether a verification step was left hanging. Not the whole log.
4. **`audit_progress.md` on demand only, by targeted search.** Approach Details are the contract; the audit is the source behind them. Pull one finding back when the approach reads ambiguous or blast radius needs confirming:

```bash
grep -n "ISSUE-014" audit_progress.md    # locate the finding
sed -n '210,240p' audit_progress.md      # read only that block
```

Never re-read the findings log end to end on a resume — Phase 0 already distilled it into the plan.
5. **Plan missing** → this is not a resume. Run Pre-Flight.

---

## Rules a Fix Must Never Break
| Rule | Why |
|---|---|
| No source edit on resume unless header Status = Approved | Approval gate must survive a context reset |
| One fix per edit | Mixing makes regressions unisolatable |
| No deviation from approved Approach Details without announcing | Silent deviation breaks the review contract |
| No public signature change unless the fix says so | Breaks callers |
| No new dependency without announcing in 3-A | Affects the whole project |
| No rewriting surrounding code outside the fix | Bypasses the safety model |
| No "Done" on a partial fix | Mark ⚠️ Partial; note what remains |
| Never skip the checkpoint | Trivial fixes have caused incidents |
| Re-read the target region fresh before editing | Files drift between plan and execution |
| Read the changed file back after editing | A fix with a syntax error is worse than none |
| Only prepend `✅ FIXED`/`⏭️ DEFERRED` to `audit_progress.md` | Altering audit data destroys the record |

---

## `fix_plan.md` Mutation Rules
| Section | Rule |
|---|---|
| Status / Updated | Overwrite on every phase transition |
| Project Patterns | Overwrite on user correction; mark *(user-corrected)* |
| Fix Inventory | Status column overwrites; other columns fixed once written |
| Approach Details | Overwrite per subsection on revision; mark *(user-revised)* |
| Execution Order | Overwrite when reordering; note reason |
| Deferred / Excluded · Execution Log | Append-only; never edit a completed entry |

---

## Project-Level Conventions

### Plans & Artifacts Folder
Place all plan documents and audit artifacts inside a dedicated folder at the project root that is gitignored. The recommended names are `.artifacts/` or `.scratch/` (add to `.gitignore`).

### Sub-Agent Usage
For broad exploration tasks (finding files, understanding file patterns, searching code), use a sub-agent with the minimum-cost model available to avoid context rot and preserve budget for the main task. For large repetitive refactors (e.g., renaming a function across 20+ files, updating the same pattern in many modules), delegate to a sub-agent with clear per-file instructions and a checkpoint after every batch. Verify each batch's output before starting the next.