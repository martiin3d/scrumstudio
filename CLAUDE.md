# Scrum Studio work coordination

Read AGENTS.md for this repository's project, MCP, ownership and file-lock rules.
Use a distinct persistent Claude identity (agent:scrum:claude-primary), or the
distinct worker actor explicitly supplied by a CLI bridge; never reuse a running
Codex worker's identity. Apply scrum-resume, scrum-work and scrum-close skills.

Prefer Small tasks with one independently verifiable result. Follow scrum-usage
at safe checkpoints: real account usage, default 5h92% /weekly94%, pause when
telemetry is unknown/stale. Stop editing before a handoff, respect project Stop,
and never bypass quota policy, permissions or another agent's file reservations.
