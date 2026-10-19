---
name: scrum-complete
description: Complete verified work using shared task continuation and batch policy.
---

Call work_finish(project,id,actor,token,evidence,checklist?,continuity?,log?,settings_seen?,task_seen?,measurement_session?). Evidence states actual relevant checks; omit unchanged checklist and duplicate log. Use In Review when human approval remains, Blocked for impediments.

completed commits Done, releases locks and resolves blockers before selecting next. Reuse returned next; next_error never undoes Done. No automatic next claim.

When ending, include close_session=true,client. Omit retrospective_items unless there is a new process lesson; review_items is only for real human follow-up. Optional notes are linked to the completed task. Evidence states actual checks only; do not copy the human final response or write no-new-finding prose. Other own work needs explicit handoff first. Saved note IDs and session.status=offline finish closure; on close_error repair only missing steps.

Only when actual task-attributable client telemetry already exists, usage_report can accompany this call; load scrum-metrics for its schema. Never fetch quota or ask a model to estimate cost solely for this field.

For shared evidence, continuation and managed batch rules, load [core guidance](../scrum-work/references/core-guidance.md).
