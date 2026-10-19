---
name: scrum-close
description: Close a Scrum Studio session using the shared handoff policy.
---

If work_finish returned session.status=offline, stop: closure is complete. close_error requires only missing repairs, never repeated Done or notes.

For unfinished work, preserve useful result/checks/next step with optional task_update(continuity), then release own claims for resumption. Never release another worker. Quota/manual pauses use agent_checkpoint(action=pause,client,summary) after editing stops; its automatic handoff already saves task continuity and releases work.

On ordinary standalone closure call agent_checkpoint(action=offline). Retrospective is optional for a new process lesson; Review only for real human follow-up. Link task-specific notes with task=id. No mandatory Daily/chat or duplicate per-task notes. Export for authorized Git handoffs; never version .scrum/local. No automatic wakeup or idle polling.

Stop handoff: the bridge confirms its CLI has exited before cleanup. Each claimed task returns to TODO with one bounded continuity summary of recorded progress and the next step. Checklist, acceptance, planned files and evidence remain in their task fields. Own task/file locks are released; concise Chat/activity references locate that continuity without copying it. No note is generated. No automatic Daily or Retrospective is written. Captured final text is for human Chat only and is not copied into task continuity; never invent missing progress. Existing records are reused without another model call. Stop does not mark work Done. Repeated pause without claims adds no notes.

For shared continuation and handoff rules, load [core guidance](../scrum-work/references/core-guidance.md).
