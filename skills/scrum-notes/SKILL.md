---
name: scrum-notes
description: Record Scrum Studio human notes or manage Review note checkboxes through MCP.
---

Use the scrum-studio MCP and the intended project ID. Resolve the project with `projects` only if unknown; clarify ambiguous matches. Write stored notes in English.

- Human Notes combines the old Daily/Retrospective/Review pages; legacy kinds remain for compatibility. Call `note_add(project, kind, title, items, task?, audience?)` with exact kind `Daily`, `Retrospective`, or `Review`. `items` is a list of nonempty strings, one point per item (max 100 items, 4000 characters each).
- Daily: capture known progress, next work and blockers. Do not invent activity or make all sections mandatory when no relevant information exists.
- Retrospective: record observed process issues and concrete improvements, each as a bullet item. Use task=id for a task-specific lesson; omit task only for genuinely project-wide guidance. No new lesson means no retrospective. Use [scrum-close](../scrum-close/SKILL.md) for closing a work session.
- Review: record human decisions, checks and follow-ups with relevant task IDs and concise evidence. New Review notes start not done.
- Use `note_check(project, id, done=true|false)` to mark a Review note done or not done. For a known task, resolve IDs with `task_get(project,id,include_notes=true)` (paginated notes_after/notes_limit); request notes_content=true only for needed content. Use snapshot only for an explicitly required project-wide audit. The checkbox applies to the whole note, not individual bullet items; use separate Review notes when actions need independent tracking.
- The MCP appends notes; it cannot edit existing note text or check Daily/Retrospective notes. Human-requested note_cleanup is a separate narrow maintenance exception. Add a clearly titled correction if needed rather than claiming to modify history.

Reuse session context, avoid duplicate notes, and do not load full history unless the request requires it. Report the saved note ID or checkbox result briefly.

Task links are optional and must belong to the same project. Historic unlinked notes remain project-wide. Notes are references, not mandatory copies of a result already saved as task evidence/continuity; reuse text rather than generating another model summary. Human pages retain Daily/Review/Retrospective history.

For shared task continuity and AI reminder rules, load [core guidance](../scrum-work/references/core-guidance.md).

Only on an explicit human cleanup request: note_cleanup(project,actor=<active human>) previews counts of obsolete human Daily/Retrospective notes and automatic Reviews titled Agent pause handoff. confirm=true writes a durable ignored local JSON backup before deleting just those project rows. It preserves task state/evidence/history, other human Reviews, AI reminders and other projects. No backup or deletion on empty repeated cleanup. Never call this as routine worker bookkeeping. Recovery can use the saved note fields through note_add after explicit human authorization.
