---
name: scrum-complete
description: Complete verified Scrum Studio work with evidence in one call.
---

`work_finish(project, id, actor, token, evidence, checklist?, continuity?, log?)`. Evidence lists the checks actually run; omit unchanged checklist and duplicate logs. Before this call, `task_changes(id)` once and put your diff review in `log` ("Before: …; After: …", see scrum-work step 6); also repeat it in the final answer. It commits Done, releases locks, resolves blockers and returns the next work (no automatic claim). `next_error` never undoes Done; `already_done=true` means a retry found it complete.

Last task of the session: add `close_session=true, client` (retrospective_items only for a new process lesson, review_items only for real human follow-up). Other own work needs a handoff first. Batches: `use_batch=true, client`; continue only with the returned next task after a fresh claim. `usage_report` only with real task-attributable telemetry ([scrum-metrics](../scrum-metrics/SKILL.md)).
