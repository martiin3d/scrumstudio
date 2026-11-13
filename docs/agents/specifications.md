# Shared specifications

Use the task's existing description, acceptance, checklist and evidence for a localized change. Write a shared Markdown specification for work with multiple tasks that depend on the same public contract, migration or decision. Store it in the repository alongside the relevant documentation.

Link the specification and applicable section from each participating task description. Each task keeps its own independently verifiable outcome and evidence. Use Scrum dependencies for execution order. Durable decisions scoped to files can be recorded through `work_finish` decisions.

Use these headings when writing a shared specification:

1. **Outcome** describes the user-visible behavior.
2. **Contract and scope** states the interfaces, invariants, compatibility and excluded changes.
3. **Decisions** records resolved choices and links any decision still required.
4. **Acceptance and rollout** specifies concrete checks, migration order, and recovery where relevant.
5. **Tasks** links the Scrum task codes responsible for each outcome.

When the contract changes, update the relevant section and the acceptance of affected tasks. Resume from current task continuity and check whether the changed section invalidates earlier evidence.
