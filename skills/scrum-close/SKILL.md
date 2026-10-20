---
name: scrum-close
description: Close a Scrum Studio session leaving unfinished work resumable.
---

- Unfinished task: `task_update(continuity="Done: …; Next: …")` once, then release your claim. Quota/manual pause: stop editing, then `agent_checkpoint(action=pause, client, summary)`; it saves continuity and releases your work, so do not duplicate it.
- Normal end: `agent_checkpoint(action=offline)`, or `work_finish(close_session=true, client)` for the last task. `session.status=offline` means closure is complete; on `close_error` repair only the missing step.
- Notes are optional: Retrospective only for a new process lesson, Review only for real human follow-up (`task=id`). No Daily, chat recap or copies of continuity.
- Export only for authorized Git handoffs; never version `.scrum/local`. No automatic wakeup or idle polling.
