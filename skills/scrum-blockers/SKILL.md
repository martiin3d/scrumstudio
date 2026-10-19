---
name: scrum-blockers
description: Set or resolve multiple blocked-by tasks in Scrum Studio and handle automatic unblocking comments. Use for dependency planning or blocked-work maintenance.
---

Use the scrum-studio MCP with a known project and actor. Work in English.

- `dependencies` is the API field for the UI's **Blocked by** task list. Resolve task codes with `task_get(project,code)`; use returned numeric IDs, never code suffixes. Dependencies must belong to this project, with no self-links or cycles; max 100 unique IDs.
- Supply dependencies on `task_create` or replace them through `task_update`. Read the current task once with `task_get` before a partial list edit if not already known; preserve unrelated blockers. New tasks start TODO even with dependencies; set Blocked when the impediment is established.
- For a non-task impediment, use `blocked_reason`. A Blocked task needs dependencies or a prose reason. Do not clear a prose reason without evidence.
- On the first transition to Done, the server removes completed task blockers from affected dependents, records an automatic comment with actor/time, and moves Blocked tasks to TODO only when no dependencies or prose blocker remains. This runs in the same transaction: do not reproduce it with extra calls or duplicate comments. Inspect `unblocked` and `unblocked_total` from the completion response, then `next_work(project,actor)` to continue the established priority order.
- TODO does not acquire a lock. Claim before execution. In Review is not Done and must not resolve blockers early. Completing again without a state change must not add another resolution comment.
- Reopening a completed prerequisite does not restore removed links automatically. If the new work invalidates a dependent, add a justified new blocker explicitly. Never alter another worker's active task without its lock token; coordinate via comments instead.
- Cancelled prerequisites are removed from dependent blocker lists, with an authored cancellation-resolution comment. A Blocked task returns to TODO when no task dependency or prose reason remains. Remaining blockers stay intact. Do not duplicate automatic cleanup or count cancellation as delivery. New cancelled prerequisites are rejected. Time spent Blocked is recorded automatically, including the server's return to TODO.

Use [scrum-users](../scrum-users/SKILL.md) only when identity/lock handling is needed. Avoid snapshots unless planning requires the entire backlog.
