---
name: scrum-activity
description: Append work-log entries or authored comments to Scrum Studio tasks and read their paginated activity history with minimal context.
---

Use the scrum-studio MCP, the intended project, and a registered actor ID. If given a task code, resolve it with `task_get(project,code)` once. Keep stored entries in English.

- Record actual work with `task_activity(project,id,actor,token,text,kind="work")`. State the meaningful change, evidence/result, or next step in at most 2000 characters. Use concise entries per milestone, not per tool call or thought.
- Prefer `task_update(...,log="Implemented X; relevant check passed")` when also changing status, category or checklist. This records work in the same transaction and saves a call. `task_create` also accepts actor+log when initial context is useful.
- Use `task_activity(...,kind="comment",text)` for questions, decisions or human feedback. Comments require a registered actor and can be added while another worker holds the lock; they do not extend that worker's lease. Work-log writes need the claiming session's token on active locks; reclaim an expired In Progress task before recording work.
- Author IDs and UTC timestamps are stored automatically. Never fabricate attribution or include private lock tokens in text. Append corrections rather than rewriting history. System-generated creation/state/lock/resolution events already exist; do not duplicate them manually.
- History is excluded from `next_work`, `task_get` and `snapshot`. Read `task_activity(project,id)` only when needed: the default is the latest 20 entries, max 50. Use `before=oldest` for older pages, or `after=latest` for new entries. Do not supply both. Reuse cursors; never reread unchanged history or poll while idle.

Task history records progress within a task. Project Review notes still hold human handoffs; session Retrospective notes still hold process improvements.

Use the web Updates & comments section or task_activity for progress after creation. Keep the original description and acceptance criteria as the task contract. If scope actually changes, edit the contract and record why in a separate authored update.

Prefer the task continuity summary over reconstructing history on resume. Update it only for meaningful handoff/context changes through task_update(continuity,actor,token); max2000 characters covering result, relevant files, verification and next step. It is authored in activity and exported. continuity_stale warns of changed task context; do not treat it as current without checking. Use version_seen for unchanged task reads and incremental activity cursors only when history is necessary.
