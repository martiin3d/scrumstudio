# Scrum Studio core guidance (rare topics)

The normal lifecycle lives in [scrum-work](../SKILL.md). Load this file only for the topics below.

## Managed batches
Preflight may offer up to three compatible task headers. Work only on the current claimed task; the others stay TODO with unreserved files. Complete each with `work_finish(use_batch=true, client)`; continue only with the returned next task; reuse next.claim when claimed=true, otherwise claim it freshly; exit on `batch_done` or session offline. Never combine evidence or mark the whole batch In Progress.

## Model routing
Auto defaults to standard independently of task size. During existing planning choose economy for mechanical low-risk edits, standard for implementation/debugging/Unity integration, and advanced for demonstrated difficult reasoning. Set `model_tier` with a short reason when useful; a human-fixed model wins. If routing changes mid-task, save continuity and close safely; at most two relaunches. Never probe accounts or run a model for discovery.

## Verification reuse
Reuse a successful check only when source tree, runtime and check bundle are identical, and say it was reused. External state or browser validation needs fresh evidence.

## Graph-first code reading
Use codebase-memory when installed (search symbols, trace, read snippets, check coverage of each relied-on file); otherwise rg and direct reads. Expand only when callers or tests require it.

## Local MCP recovery
If the desktop MCP stalls, distinguish transport latency from storage failure with the bundled diagnostic. Use `scripts/mcp_client.py --tool TOOL` only when the local server responds and the desktop transport is stalled; same policy and reservations apply. Never blindly retry an uncertain write, expose tokens, or use a fallback to evade a rejection. Approval-required calls run sequentially.

Git preparation recovery also handles claim_deferred replies: the supervisor links a waiting task to one scoped recovery and starts it through normal claims, without another human Wakeup. Completed dependency checkpoints can publish before their context group finishes when per-task commit and Group merge are enabled; this also applies when the consumer still awaits other prerequisites, and does not finish pending tasks or permit cleanup. Nested branches keep their recorded parent destinations; inspect completed intermediate outcomes before propagating further. A genuine external recovery blocker leaves other independent work eligible.

For Git recovery blocked only by a dirty checkout, use `blocked_reason_kind=checkout`. Native preflight reopens it for verification once clean; it does not complete acceptance. Use `external` for separate permission, credential or ownership decisions. An authorized idle supervisor rechecks after bounded local metadata changes; unchanged state starts no model, and Stop/quota cancels the retry.


## Per-task Git delivery
When git_merge=Task, context groups share planning context, not branches. A verified task remains In Review until native integration/publication succeeds. Preserve its evidence; only resolve the returned Git recovery, not the implementation again. Blocked partial files are private checkpoints when enabled and are restored after parent synchronization; unrelated task branches do not include them. A real merge/restore conflict needs scoped resolution. Never mark an unfinished prerequisite Done to unblock a consumer.

Native Git completion records verified file fingerprints before committing. Recovery can close the original after a failed commit without repeating unchanged checks; publication-only notes do not invalidate that proof, but changed acceptance or source does. Explicit completion of an already integrated legacy outcome can run from another active branch without checkout or staging. Folder markers never expand commit scope; concrete planned files remain required.

Managed supervisor capacity recovery: explicit provider serverOverloaded / selected-model-at-capacity failures get at most three same-task, same-model retries after 30/90/180 seconds. The supervisor performs local waits and rechecks Stop, fresh quota, dependencies and reservations. Do not add a worker retry loop, switch models automatically or repeat valid verification. After exhaustion, preserve continuity and report the provider failure.

Checkout recovery: do not wait indefinitely for an unidentified planning owner. Inspect pre-existing documentation and task history. Reconcile/deliver verified documentation only within authorized scope and explicit file reservations; otherwise ask the concrete unresolved question. Dirty paths alone never establish authorship or authorization to publish unknown work.
