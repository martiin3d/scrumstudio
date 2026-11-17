# Coordination

- [scrum_cli.py](../../scrum_cli.py) resolves the project and actor and journals native writes. A managed worker inherits its private context from the supervisor.
- [workflow.py](../../workflow.py) composes start, claim and finish. [store.py](../../store.py) applies task state and ownership. [context_tools.py](../../context_tools.py) supplies the compact task and continuation version.
- [agent_bridge.py](../../scripts/agent_bridge.py) prepares a claimed task, constructs the worker prompt and launches the provider process. [context_batches.py](../../context_batches.py) keeps a sequential group on one checkout.
- A fresh native Light claim with planned files and acceptance criteria can start directly from the supplied scope and finish command. Project rules and the native checks for evidence, reservations and Stop still apply. Resume, batch and Git recovery keep the full startup instructions. Startup measurements record the renderer used.
- Resume from task continuity and the saved file plan. Check the current branch, changed paths and any stale-continuity marker before reusing evidence. Fetch linked decisions, attachments and diagnostics when relevant to the task. Record the next unfinished action on the same task.

For a smaller Codex worker catalog on this checkout, put a JSON array of skill directory names in `.scrum/local/codex-skills.json`, for example `["codebase-memory"]`. Add the specialists this project needs. The filter keeps `scrum-work` and preserves explicit Codex skill rules. It covers the immediate skill directories in Codex home, the user's `.agents/skills`, and the project. A skill omitted from the catalog can still be read from its known path.

For changes to this flow, run the relevant CLI, bridge, workflow or context tests listed by `python scripts/verify.py --files PATH`.

A native Codex worker can run `python scripts/skill_lookup.py "capability keywords"` from its checkout when it needs specialist or Scrum instructions. The lookup returns up to five manifests by name or description. Open the matching `SKILL.md` for the full procedure.

Declare skills by name in a task plan item, for example `"required_skills": ["codebase-memory"]`. The worker gets the matching manifest paths at startup, including on resume. Record newly needed skills with a task update when they matter for a handoff. For skills needed by every task on this machine, put their names in `.scrum/local/required-skills.json`. Task requirements travel in project snapshots. Each checkout resolves the paths locally.
