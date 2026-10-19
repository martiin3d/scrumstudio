---
name: scrum-work
description: Execute authorized Scrum Studio tasks using the shared core policy reference.
---

At startup use [scrum-resume](../scrum-resume/SKILL.md) and [core guidance](references/core-guidance.md), resolving these references relative to this installed skill directory. Before first claim, apply [scrum-rules](../scrum-rules/SKILL.md) to register or reuse the complete known authorized scope, with descriptions and acceptance criteria. Explain cohesive grouping briefly. One bounded request can be one task. Work only within authorized scope; do not add speculative backlog.

Claim the current task and planned files before edits; only claimed=true grants a token. Preserve ownership, WIP, quota and file reservations. Load [scrum-file-locks](../scrum-file-locks/SKILL.md) only for conflicts or explicit lock operations. Follow [scrum-usage](../scrum-usage/SKILL.md) at configured safe boundaries and stop when should_stop is true.

Treat next_work, task_search results, managed preflight and batch headers as navigation only. Load current execution scope with task_get or work_start(id) before editing; a header version is not full or cached execution detail. Preserve the full acceptance criteria, blockers, checklist, evidence and continuity from that current task response. Cache policy IDs, task versions and measurement_session; preserve cached data on changed=false. Costs, history, snapshots and metrics are on-demand.

Move states promptly. Post only meaningful findings, blockers or handoffs. At completion use [scrum-complete](../scrum-complete/SKILL.md); at session end use [scrum-close](../scrum-close/SKILL.md). Do not duplicate saved continuity.

Verify affected behavior proportionally with scripts/verify.py --files PATH... --run --reuse; expand for broader impact or failures. Report actual checks as evidence; do not rerun unchanged successful checks merely to fill a log. Follow core guidance for graph-first source reading when available.

When known work has several independently deliverable outcomes, refine/create the concrete set with task_plan in the existing planning turn before claiming. Reuse cached policy and existing IDs/history; a cohesive result may span several files and still remain one task. Leave independent tasks unclaimed for other already-authorized workers; do not spawn agents or add model calls, polling or per-subtask ceremonies for decomposition. task_plan changes planning only and never grants ownership.

Task attachments are references, not automatically loaded content. Use task_attachment list for availability, then get with attachment_id and include_content=true only when needed. Add/remove requires active credentials for locked work; upload explicit base64, never local paths (max5MiB, ten references). Report missing content instead of assuming an export carried blobs. See [scrum-task-update](../scrum-task-update/SKILL.md) for operations.

For shared startup, quota, ownership, continuation, verification, batch and transport recovery policy, load [core guidance](references/core-guidance.md) relative to this installed skill directory. Use its local MCP fallback only after confirming that the server responds and desktop transport stalled.

Choose optional verification_level (auto/minimal/targeted/full) and verification_reason (max 500 characters; required for explicit levels) during ordinary task planning/create/update, without a separate model call. Auto adds no default compact AI context. Minimal uses justified inspection or manual checks for low-risk changes; targeted checks affected behavior and regressions; full runs broad regressions for shared contracts, storage or unresolved risk. These record intent, not successful evidence or permission to skip mandatory checks. If automated tests are skipped, record the reason and actual manual/inspection result. Acceptance criteria and required evidence still apply; scripts/verify.py keeps its conservative safe check selection and reuse rules.
