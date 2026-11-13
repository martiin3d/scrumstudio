# Coordination

- [scrum_cli.py](../../scrum_cli.py) resolves the project and actor and journals native writes. A managed worker inherits its private context from the supervisor.
- [workflow.py](../../workflow.py) composes start, claim and finish. [store.py](../../store.py) applies task state and ownership. [context_tools.py](../../context_tools.py) supplies the compact task and continuation version.
- [agent_bridge.py](../../scripts/agent_bridge.py) prepares a claimed task, constructs the worker prompt and launches the provider process. [context_batches.py](../../context_batches.py) keeps a sequential group on one checkout.
- Resume from task continuity and the saved file plan. Check the current branch, changed paths and any stale-continuity marker before reusing evidence. Fetch linked decisions, attachments and diagnostics when relevant to the task. Record the next unfinished action on the same task.

For changes to this flow, run the relevant CLI, bridge, workflow or context tests listed by `python scripts/verify.py --files PATH`.
