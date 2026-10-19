---
name: scrum-cancel
description: Cancel or reopen Scrum Studio tasks with an authored reason, preserving history and coordinating dependent work through its MCP.
---

Resolve the intended project/task from known IDs or task_get(project,code). Cancellation is reversible and means the deliverable will not be completed; it is not evidence of success.

- Cancel when requested or clearly authorized by the agreed scope; do not cancel other workers' work to free capacity. Call `task_update(project,id,actor,token,status="Cancelled",cancel_reason="...")` with a specific English reason (max 2000 characters). Use the task's current token for an active lock. Lost/expired claims require the established recovery workflow, not impersonation.
- Cancellation releases task and file locks, keeps owner/history, and excludes the task from next_work. It does not need completion evidence and must not be counted as Done.
- The server removes the cancelled prerequisite from existing dependents and records an authored resolution comment. Blocked dependents return to TODO only when no other task dependencies or prose reason remains. Inspect the compact unblocked response, then request next_work; do not manually repeat dependency cleanup. Cancellation is discarded scope, not successful delivery. New dependencies on cancelled tasks are rejected.
- Reopen with `task_update(...,status="TODO")`. This clears the current cancellation reason when not explicitly resupplied; the original reason remains in history. Claim again before work. Do not recreate the task and lose its code/history.
- Report the task code, resulting state and reason briefly. Use scrum-blockers only if dependency replanning is actually needed.
