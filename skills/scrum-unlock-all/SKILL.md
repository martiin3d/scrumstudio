---
name: scrum-unlock-all
description: Release all of an agent's own file locks, or human-requested project-wide file locks, in Scrum Studio through its MCP.
---

Resolve the intended project and scope from the request. Stop file edits before releasing reservations; release does not undo edits or stop running workers.

- Default to own reservations: `file_locks(project,action="release",actor,all=true)`. This releases that actor's files in the selected project, including older stored reservations; it does not change task status or task lease. Use task_release too only when ending the task's claim.
- For an explicitly requested project-wide release, use the intended human profile as actor and `force=true`. Coordinate with affected active workers. An agent profile cannot force-release another worker's files; do not create or impersonate a human profile to bypass this rule.
- An ambiguous request must not expand into other workers or projects. Ask about scope only when necessary. A single call covers all matching reservations; no ID collection, pagination or per-file release loop is needed.
- Report released count and whether scope was own files or all files in the project. Reacquire files before any later edits. A concurrent worker can acquire a new lock after this transaction; do not claim the project will remain unlocked.

The File locks web page exposes Release all with own/project-wide scope. Project-wide release is a human recovery operation.
