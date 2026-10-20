# Repository instructions

For structural codebase exploration, use the installed `codebase-memory` skill when available.

## Work coordination

Use the `scrum-studio` MCP and follow the `scrum-work` skill (it covers start, resume, planning, claims, verification, finish and handoff). The board is **SCRUM**, stored in this checkout's ignored `.scrum/`; find it with `projects` by matching this checkout folder, never a hardcoded ID or path. If none exists, open/create one for this folder through the MCP.

- Identity: Codex `agent:scrum:codex-primary`, Claude `agent:scrum:claude-primary`, or the distinct worker actor a CLI bridge supplies. Concurrent workers need distinct identities.
- Resume your own unfinished tasks (`work_start` → `resume`) before planning or starting new work; never re-create or redo recorded work.
- Coordinate only through MCP tools (or `scripts/mcp_client.py`), never direct database edits. If the MCP is unavailable, report it instead of bypassing reservations.
- Never bypass Stop, quota policy (default 5h 92% / weekly 94%; unknown telemetry pauses), permissions or another agent's file reservations.
- Never commit `.scrum/` or `data/`; use `project_export` for portable handoffs and `scrum-project-storage` for opening/importing/restoring (then re-resolve IDs and task codes).
