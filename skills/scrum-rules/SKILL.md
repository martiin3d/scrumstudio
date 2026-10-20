---
name: scrum-rules
description: Apply Scrum Studio task sizing and planning rules - decide when to group or split work so one agent saves tokens and other agents can take tasks in parallel, with no duplicates or speculative backlog.
---

Before the first claim, map the whole known authorized scope to existing or new TODO tasks in one `task_plan(project, actor, items)` call (1..10 items: key, title, description, acceptance_criteria; optional id to refine an unlocked unfinished task, files, dependencies by id or key, model_tier). It is atomic and never claims. Search first (`task_search`). A same-title unfinished task returns `existing=true` and is never duplicated silently: with no `differs` it is identical, continue it; with `differs=[...]` refine it by `id` if the scope really changed; `allow_duplicate=true` only for a deliberately separate outcome.

## Group or split

The unit is **one result with its own acceptance check**, sized so a session finishes one to three tasks. Then decide with these criteria:

**Keep in one task** when any applies:
- Same result with shared verification (API + storage + regression test of one fix).
- The pieces edit the same files and must land together (splitting would only create lock conflicts).
- A piece is trivial on its own (a rename, a constant, a copy fix of a few minutes): fold it into the task that touches that code.
- One piece cannot be verified without the other.

**Split** when any applies:
- The results ship or can be reverted independently.
- They touch disjoint files or areas: another agent can take one in parallel.
- They need different model tiers (mechanical edit = economy, design or tricky bug = advanced).
- Together they would likely exceed one session (a split task survives a Stop/resume with cleaner continuity).
- Different risk: keep a risky change separate from safe cleanup.

**Never split** by file, tool call, reading/testing phase or backend/frontend layer of one result. No speculative backlog; register justified discoveries in TODO before switching to them.

## Make split tasks cheap and parallel-safe

- Always give each task an accurate minimal `files` plan. The server uses it to avoid conflicts between agents and to batch tasks that share files into one agent run (up to three, same priority and model), so related small tasks reuse context automatically while disjoint tasks go to other agents.
- Add `dependencies` only for real order constraints; a needless dependency serializes work and blocks other agents.
- Same `context_group` for tasks that share reading context; it groups them on the board without claiming anything.
- Check cached `max_parallel_agents`: with 1, lean toward fewer, cohesive tasks; with more, prefer splitting by disjoint files so agents do not wait on each other.
- State the grouping decision in one line. Sizing policy is changed only by a human (`project_settings` or the web).

Examples and Balanced/Unrestricted details: [details](references/details.md).

Git recovery tasks use the existing checkout and may preserve dirty files/staging; they remain eligible when no other live claim needs the checkout. Never bypass active claims. For direct MCP CLI fallback omit --timeout to use operation-specific bounds (15 seconds ordinary calls, 180 completion writes); never retry an uncertain write without reconciling recorded state.
