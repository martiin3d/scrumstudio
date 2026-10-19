---
name: scrum-file-locks
description: Reserve files for a Scrum Studio task before editing, detect conflicts with other agents, and release file reservations through its MCP.
---

File reservations are cooperative exact-file locks; they do not prevent external editors writing. Resolve the project workspace from cached settings or `project_settings` once. Use your stable actor ID and private task token. Distinct concurrent workers need different identities.

- Plan the smallest accurate file set, including destination paths for new files or renames. `files` on task_create/task_update stores planned paths; relative paths resolve under workspace_root. Directory locks are unsupported. Paths outside the workspace are rejected. Canonical paths collide only inside the selected project checkout. Other projects/clones do not see these locks, even for the same physical file; use separate source folders.
- Prefer `task_claim(project,id,actor,files=[...])`, or `task_update(...,claim=true,files=[...],actor)`, to claim task and files atomically. A response with `claimed=false` means no claim or partial file reservation occurred. Save the token only on success.
- For newly discovered files during work, call `file_locks(project,action="acquire",id,actor,token,paths=[...])` BEFORE editing. Acquisition checks all paths atomically. `acquired=false` reports conflicts without partial acquisition. Never rely on a prior list response as authorization to edit; the acquisition is the authoritative check.
- On conflict, do not edit the occupied file or force another worker's lock. If you already claimed the task, record the expanded files plan and move to TODO with a concise log, releasing its claims. Continue with `next_work(...,exclude_ids=[conflicting_task_id])` to avoid an immediate retry; discard temporary exclusions after useful work completes or the situation changes. No polling loops.
- Reservations expire with the task lease; renewal through task_claim, task_update or a work-log append renews current reservations. Stop editing if ownership/renewal fails. Do not keep working after lease expiry.
- Leaving In Progress or explicitly releasing the task also releases its file reservations. To release selected own files earlier, list only if IDs are unknown, then `file_locks(project,action="release",actor,ids=[...])`. Reacquire before editing those files again. Never publish tokens.
- Cancelled also releases reservations; it is not a shortcut to Done and requires an authorized reason. Reopen before claiming discarded work.

Use [scrum-resume](../scrum-resume/SKILL.md) at every session start and [scrum-unlock-all](../scrum-unlock-all/SKILL.md) only for bulk release. Do not scan all locks when an atomic claim already checks your planned paths.
