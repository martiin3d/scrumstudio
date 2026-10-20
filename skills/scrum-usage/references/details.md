# Scrum usage: full details

Load only for monitor setup, auto-resume or policy changes.


At session startup and at safe editing boundaries no farther apart than cached usage_check_seconds (default60), obtain actual provider USED quota percentages. These are account usage windows, not context size, tokens in this chat, elapsed session time or remaining percent. Defaults stop at 5h>=92% OR weekly>=94%. Missing, reset or stale telemetry pauses guarded folder agents; never invent zero or disable the guard to proceed.

Codex desktop: use get_usage_limits when exposed. Inspect windowDurationMins: 300 identifies5h and10080 weekly; do not assume primary always means5h. Prefer rateLimitsByLimitId and conservatively retain the highest relevant usedPercent. Map resetsAt if available. Alternative local CLI: run scripts/agent_bridge.py --project ID --client codex --usage-once from the installed Scrum Studio checkout; it reads account/rateLimits/read without a model request.

Claude Code: use the actual rate_limits.five_hour/seven_day.used_percentage and resets_at supplied to its status line, via scripts/claude_usage.py and a local ignored JSON feed. Preserve observed_at from the feed. If this client/version supplies no reliable data, report unknown and pause; do not scrape credentials or use context-window percentages as quota.

Call agent_checkpoint(project,actor,client,session_used_percent,weekly_used_percent,observed_at?,session_resets_at?,weekly_resets_at?,summary?) only at a safe boundary after editing has stopped. summary states verified progress, unfinished work and next check. When should_stop=true, stop all project work immediately: the call already moves your claimed work to TODO, releases own task/file locks and saves task continuity and concise activity/chat references when live work existed. Do not duplicate those notes. With no live task, follow scrum-close once if there is session context to preserve. New claims/renewals are blocked while stopped; cancellation/Done must never replace an honest pause.

Split long commands into bounded steps when practical; reserve headroom for handoff. Cooperative checks and provider update delay cannot guarantee an exact hard cutoff at a percentage. An optional CLI bridge monitors outside the model, interrupts only its own worker and saves handoff after that process exits. Desktop sessions must cooperate at checkpoints. Use scrum-control to connect a bridge; no unchanged model polling loops.

Only a human-requested change may set session_stop_percent, weekly_stop_percent (1..100), usage_check_seconds (1..300) or usage_guard through project_settings with that human actor. Human policy is shared by all project agents; account usage may also include work in other projects.

usage_guard=false only permits missing/stale telemetry; known fresh usage at either configured threshold still stops work. Do not clear it without the human explicitly requesting that policy change.

Optional local monitor: python scripts/usage_monitor.py --project ID --actor ACTOR --client codex [--once]. It launches no model/worker, writes only .scrum/local/usage-ACTOR-codex.json and refreshes at project usage_check_seconds. Start only for an existing active agent. Without --once it runs until Ctrl+C. No login startup service is installed. Claude requires --usage-file and is never invoked by this monitor; respect account restrictions. Missing telemetry remains unknown.

agent_checkpoint and work_start automatically consume that actor/client feed when explicit quota values are absent. Original observed_at is preserved so stale data cannot become fresh by rereading it. Continue cooperative safe-boundary checkpoints for Stop and handoff; the monitor itself neither interrupts a desktop agent nor claims/releases work. If CLI quota is unavailable, use the actual desktop get_usage_limits reading. Never treat bytes/elapsed coordination time as provider quota.

Human opt-in auto_resume_session_quota (default false) lets the existing authorized CLI bridge resume once after a recorded five-hour quota pause. It requires fresh provider observations after the old reset, a later future session reset, and both session/weekly windows below policy with known future reset times. Stop, manual pause, profile deletion or disabling the option cancels it. The bridge starts a fresh worker with normal work_start and claims; stale tokens are never reused. Desktop chats and telemetry-only monitors are unsupported for launching. Remain stopped when should_stop is true; automatic resume is the supervisor's job, never an agent polling loop or a reason to weaken policy.
