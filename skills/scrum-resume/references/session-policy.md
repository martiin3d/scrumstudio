# Resume policy

For shared startup, quota, ownership, selection and continuity rules, load [core guidance](../../scrum-work/references/core-guidance.md). Match the checkout and persistent actor, resume with actual quota, review own locks, and stop when policy says stop.

Session-specific details:

- Preserve cached settings, versions and measurement session when responses say unchanged; inspect only task-specific pending AI reminders.
- next_work and batch headers are navigation only. Load current full scope with task_get or work_start(id) before editing; retain its acceptance criteria, blockers, checklist, evidence and continuity.
- Unknown or stale quota pauses guarded work. Never replace the original telemetry timestamp or recover a lost task token with a new identity.
- If a write stalls, inspect state before retrying. Use the bounded local transport only after confirming the local server responds and the desktop transport is the issue.
- A known authorized Light task may be claimed in startup only when the response says claimed=true; otherwise explicitly claim the selected task and files before editing.
