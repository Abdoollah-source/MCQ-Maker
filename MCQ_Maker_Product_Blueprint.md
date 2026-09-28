# MCQ Maker — Product Blueprint & Delivery Tracker

**Status:** Proposed product design; not a claim that features are implemented.  
**Last updated:** 2026-09-26  
**Purpose:** Stable reference for user experience, reliability rules, and incremental delivery. Preserve the working single-lecture generator; use this document when planning the persistent folder queue and later multi-tab processing.

## 1. Product promise

MCQ Maker should let a medical educator choose a folder of lecture PDFs, select generation settings once, and produce validated standalone HTML exams with minimal supervision. Connectivity, provider errors, app restarts, and individual bad PDFs must not erase completed work or cause unintentional duplicate generation.

**Non-negotiable:** Every lecture is an independent, durable job. A job is *Completed* only after the existing quiz validator passes, the established HTML-saving pipeline succeeds, and the output file is confirmed. A returned HTTP 200 or parsed JSON alone is not completion.

## 2. Primary user journey

1. **Create batch:** Select input folder (PDFs), output folder, authoritative system prompt/reference, model and Thinking Level; preview detected lectures and any duplicate/unsupported files.
2. **Review:** Display lecture count, chosen settings, destination, expected output behavior. Make conflicts visible *before* starting.
3. **Start:** Create a durable queue and run one worker initially. Each lecture uses the proven single-lecture calibration → upload → generate → extract → validate → save pipeline, with appropriate conversation isolation.
4. **Observe:** Show overall *completed / total* progress and each lecture's actual stage and last verified action; distinguish provider generation time from automation overhead. Never display invented percentage for a stage that has no measurable progress.
5. **Control:** Allow safe pause/resume, retry selected recoverable jobs, inspect job details, open output folder and cancel an active job with explicit outcome uncertainty.
6. **Recover:** After restart, show a concise recovery summary, protect completed outputs and resolve interrupted submissions without blindly re-sending them.

## 3. Proposed UI: Batch Generation screen

### Header / setup panel (top)
- **Input Folder** — folder picker; show path, PDF count, inaccessible/unsupported files.
- **Output Folder** — folder picker; show permission/conflict warnings.
- **Generation Profile** — existing authoritative prompt/reference, model and thinking configuration; preserve a durable configuration fingerprint for batch identity.
- **Worker Count** — initially locked at **1**; later 1–N after multi-tab work is proven. Avoid a misleading multi-tab UI before implementation.

### Main toolbar (immediately below setup)
- **Start Batch** — validates inputs and creates/starts a durable queue; disabled for invalid configuration or when the same batch is already running.
- **Pause After Current** — stop claiming new jobs; allow active work to finish and save; label changes to **Resume Batch** once paused.
- **Resume Batch** — resume pending jobs; do not blindly restart jobs with uncertain submission outcomes.
- **Cancel Current** — ask for confirmation; attempt local cancellation; warn that a previously submitted Gemini request may still finish remotely. Preserve the job as Interrupted/Needs Attention where the outcome is unknown.
- **Open Output Folder** — open destination; enabled when available, independent of generation success.

### Summary strip
- Overall progress: `Completed / Total`; counts for **Running**, **Pending**, **Needs Attention**, **Failed**, **Interrupted**; optional measured elapsed time. An ETA is optional and must be labeled approximate if used.

### Job table (main area)
Columns: lecture name; state; stage; attempts; last update; question count for saved exams; action/details. Offer filter by state and a compact details pane. Avoid showing confidential question text in logs.

A row's **Details** opens a timeline of verified checkpoints, sanitized error category, destination file and allowed next action.
Row actions as applicable:
- **Open Exam** — for Completed jobs only.
- **Retry** — when it is safe to submit again (e.g., failure proved pre-send); never silently retry unknown outcomes.
- **Recover Session** — where a known conversation/session can be restored and inspected safely.
- **Regenerate** — intentional new run; show conflict/overwrite choice; never destroy a previously completed output by default.
- **Skip / Unskip** — explicit user decision; preserve record.

### Bottom status area
Current worker stage and clear connectivity/provider status; minimal actionable warning. Never imply completion from a successful Send click.

## 4. Durable job lifecycle

Core states: **Pending → Running → Completed**; **Failed**, **Needs Attention**, **Interrupted**, **Paused** (batch-level), and optionally **Skipped**. Store detailed *stage* separately rather than multiplying job states.

Recommended durable stage checkpoints:
1. Job created, input/config fingerprint saved.
2. Browser and Playground ready.
3. Configuration verified.
4. Calibration submitted / calibration final answer confirmed.
5. Lecture upload acknowledged.
6. Generation submitted (outcome may be unknown after disconnect).
7. Complete final answer captured.
8. A unique quiz candidate validated with the existing `validate_quiz()`.
9. Output written and verified; **only then Completed**.

Checkpoint updates should be transactionally safe and idempotent. Record output path and relevant hashes. Never infer remote success from a local process surviving or an HTTP 200 alone.

## 5. Error and connectivity behavior

| Situation | Safe response | Queue impact |
| --- | --- | --- |
| Slow but progressing navigation/generation | Stage-specific observable waits and bounded deadlines; no speculative resubmission | Keep current job running |
| Network failure **before confirmed Send** | Mark waiting/retryable with sanitized reason | Pause global worker when connectivity is unavailable; retain pending jobs |
| Disconnect **after Send** with uncertain outcome | Preserve conversation reference/checkpoint; inspect original session when feasible; otherwise Needs Attention | No blind duplicate Send |
| AI Studio initialization rejection / auth failure / permission denied | Record sanitized RPC category/status and stop submissions | Pause batch globally; present actionable error |
| Provider rate limit / temporary outage | Honor provider guidance where available; bounded cooldown without bypassing limits | Pause worker, keep durable queue |
| Bad or inaccessible PDF | Mark that job Failed/Needs Attention | Continue with other jobs when safe |
| Valid JSON syntax but wrong quiz schema | Use only a deliberately implemented, bounded same-conversation correction; validate again | Never save invalid exam; escalate if unsuccessful |
| Multiple distinct valid candidate arrays | Ambiguous; do not guess | Needs Attention |
| Valid quiz but output write fails | Preserve validated result safely where design permits; retry local save, not expensive remote generation | Job remains not Completed |
| Application/Windows crash | Reconcile durable checkpoints/output files on restart | Resume verified work only |

**Global versus per-job faults:** A corrupt PDF is local to one job. Authentication/provider-wide failure should pause dispatch for the entire queue. Never turn ambiguous outcomes into automated duplicate generation.

## 6. Restart and recovery experience

On app reopen, show a recovery banner: e.g., `7 completed · 1 interrupted · 4 pending`. Completed files are preserved and verified. Interrupted jobs display their last *confirmed* stage. If submission may have happened, prioritize inspecting/recovering that conversation; ask before regenerate when it cannot be verified. If the PDF, prompt, reference, model, or relevant configuration changed, do not silently treat an old output as the result of the new settings.

Where feasible and secure, keep a temporary validated output checkpoint so a transient filesystem error requires only **Retry Save** instead of re-uploading/regenerating.

## 7. Rollout roadmap and acceptance gates

- [x] **R0 — Proven single-lecture flow (historical):** Prior real runs created validated standalone exams; this is evidence of feasibility, not proof that the latest changed code has passed live acceptance.
- [x] **R1 — Prompt and extraction hardening (offline):** JSON-only lecture rule; balanced array candidates; select exactly one distinct validator-approved quiz; fail safely on ambiguity.
- [x] **R2 — UI/wait optimizations (offline):** Single-pass model/thinking picker logic; fixed Thinking Level verification race; pre-navigation initialization listener; stage-specific waits/diagnostics. Live re-verification remains pending due connectivity.
- [ ] **R3 — Persistent single-worker queue:** Create/durable-store jobs; sequential processing; checkpoint at confirmed save; restart recovery; per-job/global fault policies; minimal usable Batch Generation UI; mocked/offline integration tests.
- [ ] **R4 — Integrated acceptance:** One purposeful live batch (small number of lectures) when connectivity permits; confirm real resume and no duplicate outputs; investigate only reproducible blockers.
- [ ] **R5 — Multi-tab scheduler:** N independent workers with atomic job claims, session isolation, bounded concurrency, resource controls and recovery; start with two workers in live verification.
- [ ] **R6 — Product refinement:** UI polish, actionable recovery details, safe optional correction review, timings, optional ETA and performance tuning based on measured bottlenecks.

### R3 minimum acceptance criteria

- Queue/job state survives application restart.
- Exactly one worker claims a job in the single-worker phase; queue design can later support atomic claims.
- Completed jobs have validated saved HTML files and are not unintentionally regenerated or overwritten.
- Cancellation, Pause After Current and Resume have distinct semantics and tests.
- Bad PDF fails only its job; auth/provider-wide failure pauses dispatch.
- Unknown post-Send outcomes do not trigger automatic duplicate submissions.
- Browser integration uses the existing proven path; no competing validator or exam generator.
- Tests cover restart, changed inputs/config, missing output, output-name collision, save failure, pre/post Send disconnect, pause/cancel, and batch progress accounting.

## 8. Decision log / change policy

**Accepted product direction:** Persistent sequential queue before multi-tab processing; preserve verified work; Pause After Current and explicit recovery controls are first-class UX features.

**Open design decisions:** exact persistence layer after repository inspection; how to identify/restore AI Studio conversations; optional storage of sanitized validated quiz checkpoints; default retry/backoff policy; whether the existing UI architecture already offers a suitable batch screen.

**Rule:** This document is the agreed product blueprint, not a substitute for reading the actual repository. Codex should update implemented-status checkboxes and add short decision notes as it builds. Do not silently mark planned features Done because their offline mocks passed; distinguish implemented, offline-tested and live-verified.

**Suggested repository placement:** `docs/MCQ_Maker_Product_Blueprint.md`; add a short pointer from the root `AGENTS.md` so future Codex tasks read it. Track implementation in the existing GitHub Issues/Project if configured, using the R3–R6 milestones above rather than duplicating conflicting task lists.
