# Repository instructions

For structural codebase exploration, use the installed `codebase-memory` skill when available.

## Work coordination

Prefer the first-party native `scrum.py` CLI for Scrum coordination; use the `scrum-studio` MCP when explicitly selected. Follow the `scrum-work` skill (it covers start, resume, planning, claims, verification, finish and handoff). The board is **SCRUM**, stored in this checkout's ignored `.scrum/`; find it with `projects` by matching this checkout folder, never a hardcoded ID or path. If none exists, open/create one for this folder through the selected coordination channel.

- Identity: Codex `agent:scrum:codex-primary`, Claude `agent:scrum:claude-primary`, or the distinct worker actor a CLI bridge supplies. Concurrent workers need distinct identities.
- Resume your own unfinished tasks (`work_start` → `resume`) before planning or starting new work; never re-create or redo recorded work.
- Coordinate first through the native `scrum.py` CLI; use MCP tools (or `scripts/mcp_client.py`) only when explicitly selected by the project/supervisor; never direct database edits. The CLI uses the same validation, ownership, Stop/quota and Git rules. If the MCP is unavailable, report it instead of bypassing reservations.
- Never bypass Stop, quota policy (default 5h 92% / weekly 94%; unknown telemetry pauses), permissions or another agent's file reservations.
- Never commit `.scrum/` or `data/`; use `project_export` for portable handoffs and `scrum-project-storage` for opening/importing/restoring (then re-resolve IDs and task codes).
