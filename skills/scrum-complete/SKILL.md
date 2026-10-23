---
name: scrum-complete
description: Complete verified Scrum Studio work with evidence in one call.
---

`work_finish(project, id, actor, token, evidence, checklist?, continuity?, log?)`. Evidence lists the checks actually run; omit unchanged checklist and duplicate logs. Review proportionally to risk, reusing any diff already inspected; request `task_changes` only when needed. Record material findings in optional `log`, without mandatory Before/After prose. With per-task Git merge enabled, it retains verification in In Review until native commit, merge and configured publication succeed; only then does Done resolve dependencies. Other Git policies retain their configured completion behavior. Locks are released and next work is returned (no automatic claim). `next_error` never undoes Done; `already_done=true` means a retry found it complete.

Last task of the session: add `close_session=true, client` (retrospective_items only for a new process lesson, review_items only for real human follow-up). Other own work needs a handoff first. Batches: `use_batch=true, client`; continue only with the returned next task after a fresh claim. `usage_report` only with real task-attributable telemetry ([scrum-metrics](../scrum-metrics/SKILL.md)).

Git recovery exception: a task whose category is `Git recovery` authorizes its scoped integration/merge and normal push. Inspect current refs, plan/reserve files before editing, preserve other work and staging, and never force push. Do not repeat the original implementation. Record an external blocker and exit if recovery cannot proceed; do not retry in a loop. Ordinary tasks leave enabled Git automation to the server.

If `integration_pending=true`, do not repeat verified implementation or tests on unchanged source. Follow the returned scoped Git recovery; successful recovery reconciles the original task natively. Context groups organize work but per-task merge mode uses isolated branches.
