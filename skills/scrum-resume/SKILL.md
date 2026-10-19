---
name: scrum-resume
description: Start or resume a Scrum Studio session using shared policy and safe optional Light claiming.
---

Use [shared resume policy](references/session-policy.md), which links to [core guidance](../scrum-work/references/core-guidance.md); resolve both relative to their respective installed skill directories. Match the checkout folder and reuse persistent identity. Call work_start(project,client,actor or identity+name,settings_seen?,task_seen?) with actual quota per [scrum-usage](../scrum-usage/SKILL.md). Cache returned measurement_session. Concurrent workers need distinct identities.

For stalled MCP calls, storage errors or approval decisions, follow the transport recovery guidance in the installed scrum-work core reference. Do not repeat a potentially committed write or use fallback transport to bypass policy.

Review returned own file locks. Release only obsolete own locks; paginate session_resume if has_more. should_stop ends work. Missing/omitted cached fields stay cached; next.no_candidate is not proof that the backlog is empty.

Known authorized Light work may pass id,claim=true,files. Only claim.claimed=true grants a token; old locks, conflicts or Standard defer claiming. Otherwise task_claim explicitly. Never recover a lost token with another identity. verbose=true is for diagnostics only.

Treat next_work, preflight offers and batch headers as navigation only. Load current full scope through task_get or work_start(id) before editing; a header version does not carry acceptance criteria or other execution detail. Preserve the current task's acceptance criteria, blockers, checklist, evidence and continuity. Resume from its continuity and keep verified work unless changes invalidate it. Default task reads omit note history. Request task_get(include_notes=true) for references, notes_content=true for necessary content only. next_work never returns retrospectives or human notes. pending_ai_notes is only a counter; when nonzero, load pending task reminders with include_notes=true,notes_content=true (notes_audience=agent by default), then mark reminders done once resolved. Do not reload cached history or rewrite previous summaries.

