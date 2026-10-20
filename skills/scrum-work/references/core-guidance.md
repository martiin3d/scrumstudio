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
