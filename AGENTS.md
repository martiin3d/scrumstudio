# Repository instructions

For structural codebase exploration, use the installed `codebase-memory` skill.

## Work coordination

Use the `scrum-studio` MCP for work in this repository. The workspace project is **SCRUM**, stored locally under this checkout’s ignored `.scrum/`. Discover its local registry ID by matching the current checkout folder, never a hardcoded numeric ID or workstation path. Reuse the matching project. A clean clone contains no board: if no project is registered for this checkout, create/open one for this folder through the MCP rather than assuming an existing board.

Use `scrum-work` for execution, `scrum-resume` at every new session, and `scrum-close` before ending. Reuse logical worker identity `agent:scrum:codex-primary` for sequential primary-agent sessions; concurrent workers must register distinct persistent identities. Review obsolete own file locks, read relevant unfinished task continuity, and respect project WIP policy before editing.

Create justified new work in TODO. Claim the task and its planned files before editing, move states promptly, and submit actual verification evidence before Done. Cancelled removes its dependent blocker links and returns fully unblocked Blocked tasks to TODO with an authored comment; it does not count as successful completion.

Use MCP tools or its stdio JSON-RPC transport, not direct database edits, for coordination. If unavailable, report the connection problem rather than silently bypassing reservations. Prefer next_work and task_get over snapshots; cache settings IDs and fetch history/metrics only when needed. A request to create a backlog alone does not authorize starting implementation.

Project data and file reservations are isolated per checkout. Export portable JSON with project_export when a handoff needs it; this application repository excludes the entire .scrum/ board and data/ from Git. Never commit its development board, local registry, locks or sessions. Use scrum-project-storage for opening/importing/restoring folders. After import or restore, discard cached database IDs/cursors/tokens and resolve task codes again.

Prefer Small tasks: one independently verifiable outcome, no trivial fragmentation or speculative backlog. Follow scrum-usage at startup and bounded checkpoints: actual provider 5h/weekly quotas, default stop at92%/94%, unknown or stale telemetry pauses work. Stop editing before agent_checkpoint handoff, then respect should_stop without continuing. Project Stop/Wakeup signals and CLI bridges are described by scrum-control; never treat Wakeup as permission to bypass quota or ownership.

## Plan the authorized scope before execution

Before claiming the first task for a request, apply `scrum-rules`: map the entire currently known authorized scope to existing or new TODO tasks and finish registering that set first. Each new task needs its description and acceptance criteria at creation. Prefer one independently verifiable outcome per task; briefly explain cohesive grouping. One bounded request can remain one task; measurement may be acceptance evidence or a separate deliverable. Register justified discoveries before switching to them, without speculative fragmentation or retroactive planning. Backlog-only requests do not authorize execution.

AI memory is task-scoped: retain verified evidence, remaining work and next step on the task. Human notes and final chat explanations are not startup context. Only explicitly marked pending task-linked AI reminders are read on demand; resolve them once addressed. Do not require Daily/Retrospective/Review prose or reread completed history.
