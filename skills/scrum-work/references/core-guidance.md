# Scrum Studio core guidance

Load this reference from a Scrum skill when executing or resuming project work. Operation-specific details stay in the relevant specialist skill.

## Startup, ownership and selection

At each new session, resume the matching checkout and persistent logical actor with fresh actual provider quota. Review only your own locks and release obsolete ones. Respect Stop, quota, task dependencies, project WIP and file reservations. A selection or preflight offer is not ownership: claim the current task and its planned files and proceed only after claimed=true. Never take another worker's reservation or claim future batch tasks.

Before first claim, map the full known authorized scope to existing or new tasks with descriptions and acceptance criteria. Register that set first; do not add speculative backlog. Apply configured sizing, reuse tasks and keep independent results independently verifiable.

Treat next_work, task_search results, managed preflight and batch headers as navigation, not execution scope. Before editing, load the current full task through task_get or work_start(id); a header version is not full or cached execution detail. Preserve all current acceptance criteria, blockers, checklist, evidence and continuity when resuming. Prefer compact responses and retain cached versions, settings and measurement_session when unchanged. Read task continuity and existing checks before resuming; load linked AI reminders only when the pending count requires them. Attachments, history, costs, snapshots, metrics and human notes are on-demand. Read only the specialist skill needed for the requested operation.

## Quota, Stop and handoff

Use actual provider used-percent readings for the five-hour and weekly windows, not context size or a guessed estimate. Use the installed bridge's --usage-once path when appropriate; preserve the original observed_at. Unknown, reset or stale readings pause guarded work. Check quota and Stop at configured safe boundaries. When should_stop=true, stop editing and follow the automatic handoff; do not finish or cancel work to evade a stop. Never poll models.

Keep one canonical task continuation: verified result, unresolved work and next action. Preserve valid checks. Save only new facts needed to resume, once, on that task. Task evidence/checklist/files belong in their task fields. Human-facing final text and Chat are not AI memory. Notes default to human audience; only explicit unresolved AI reminders belong in agent Review notes, and mark them done once resolved. Do not create routine Daily, Retrospective or Review notes.

At safe boundaries use the configured usage checkpoint. Stop editing before pausing/offlining; the handoff releases only your own task and file locks and retains unfinished task facts. Do not duplicate the checkpoint's continuity or closure actions.

## Verification and completion

Verify the affected outcome proportionally and report actual checks as evidence. Reuse a prior successful repository check only when source tree, runtime and check bundle are identical; never present reuse as a new run. External state or browser validation needs fresh evidence. Required evidence and checklist policy still applies.

Complete through work_finish with current actor, token and evidence. In a batch use use_batch=true,client; do not close the session between members. Claim only the returned next task after a fresh claim. Exit on batch_done or session offline; do not select unrelated backlog. For non-batch work, follow the returned selection or close as instructed. Repair only missing closure steps after an error; never repeat a committed completion.

## Graph-first code reading

For structural code exploration, use codebase-memory when installed: search for relevant symbols, trace the path, then read snippets. Call coverage checks for every relied-on file. If graph coverage omits ranges, inspect those source lines directly and qualify conclusions. Never assume codebase-memory exists; use rg, file listing and direct source reads as fallback. Expand from relevant ranges only when callers, tests or missing context require it.

## Managed batches and routing

Managed preflight may provide up to three compatible task headers. Work only on the current claimed task; future tasks stay TODO and their files are not reserved. The server rechecks dependencies, ownership, model and file conflicts at each completion. Stop/offline/failure leaves unfinished work resumable; never combine task evidence or mark the whole batch In Progress.

Choose an automatic model tier during the existing planning/update action when useful; a fixed human model choice wins. If routing changes during work, record continuity and close safely before further edits; respect the bounded relaunch policy. Never probe accounts, run a model for discovery, or bypass normal sandbox approvals.

## Local MCP recovery

If desktop MCP stalls, first distinguish transport latency from local storage failure using the bundled diagnostic. Use the checkout's bounded stdio client only when the local server responds and the desktop transport is stalled; it follows the same policy and reservations. Never retry an uncertain write blindly, expose tokens, weaken permissions, or use a transport fallback to evade a reviewer rejection. Approval-required calls must complete sequentially.
