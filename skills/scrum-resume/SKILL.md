---
name: scrum-resume
description: Start or resume a Scrum Studio session and continue unfinished work from its saved continuity instead of repeating it.
---

Follow steps 1–2 of [scrum-work](../scrum-work/SKILL.md): `work_start` once with real quota ([scrum-usage](../scrum-usage/SKILL.md)), then continue the tasks listed in `resume` before anything new.

- Review `own_file_locks`; release only obsolete own locks; paginate `session_resume` if `has_more`.
- If you know the task to continue, `work_start(id, claim=true)` loads and claims it in the same call. Otherwise read its `continuity` and evidence with `task_get`, claim it again, inspect partial edits, then finish the unchecked work. Do not re-plan, re-create or re-verify what is already recorded.
- `interrupted=true` (claim expired without handoff) and handoff continuity both list the files edited on disk: `task_changes(id)` returns their uncommitted diff in one bounded call. Start from that diff plus `continuity`, not from a fresh exploration; its `unplanned` list tells you which checkout changes are not this task's.
- `next.no_candidate` is not proof that the backlog is empty. `verbose=true` is for diagnostics only.
- A stalled write: inspect state before retrying; never repeat a possibly committed write.
