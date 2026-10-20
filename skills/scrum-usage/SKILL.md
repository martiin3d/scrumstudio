---
name: scrum-usage
description: Enforce Scrum Studio five-hour and weekly quota stops for Codex or Claude agents with real provider telemetry and safe handoffs.
---

At startup and at safe boundaries (about every cached `usage_check_seconds`, default 60), use the real provider USED percentages of the 5h and weekly windows; never context size, chat tokens, elapsed time or guesses. Defaults stop at 5h≥92% or weekly≥94%. Missing or stale telemetry pauses guarded work.

- **Codex:** `get_usage_limits` (windowDurationMins 300 = 5h, 10080 = weekly; keep the highest usedPercent), or `scripts/agent_bridge.py --project ID --client codex --usage-once`.
- **Claude Code:** the status line command `python scripts/claude_usage.py --output <checkout>/.scrum/local/claude-usage.json` writes rate_limits; `work_start`/`agent_checkpoint` read that file automatically when you pass no percentages. If it is absent or stale, report unknown and pause; never scrape credentials.

`agent_checkpoint(project, actor, client, session_used_percent?, weekly_used_percent?, observed_at?, summary?)` only after editing stops; keep the original `observed_at`. With `should_stop=true` stop immediately: the call already moved your claimed work to TODO with continuity and released your locks. Never mark Done/Cancelled to evade a stop. Policy changes (thresholds, usage_guard, auto-resume) are human-only. Monitor, bridge and auto-resume details: [details](references/details.md).
