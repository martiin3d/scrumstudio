---
name: scrum-project
description: Create or select a project in Scrum Studio through its MCP. Use for project setup or discovery, not for scaffolding source code.
---

Use the scrum-studio MCP; keep project names and descriptions in English.

- Call `projects` once unless the intended numeric project ID and folder are already known. Match the folder as well as UUID/name: clones share UUIDs but have different local IDs. Reuse a matching project when the user wants to select it. If multiple matches are plausible, clarify before writing.
- For a new project, call `project_create(name, path, description)`. Keep the name concise; capture the goal and relevant scope in the description. The existing absolute folder receives .scrum metadata and local databases; source code is not scaffolded and implementation is not authorized.
- Before creating, check for an existing match to avoid accidental duplicates. Create a separate matching-name project only if the user intends it. After an uncertain write result, inspect `projects` before retrying.
- Return the project ID and name. Retain the ID for subsequent calls. Do not create tasks unless requested; when requested, use [scrum-task-create](../scrum-task-create/SKILL.md).
- Registration and any requested task seeding are intermediate steps, including when done through the native CLI. Review the onboarding plan in Scrum Studio and have an authorized human approve it before applying it. The plan can also create a first task, so review that proposal against any tasks already seeded.
- Open **Wiki → Project setup** on the registered project to review and apply the plan. Reuse the existing project and its documentation. If application was interrupted, reopen the saved plan there and choose **Resume application**. Verify that **Wiki** opens with `docs/agents/index.md` before reporting setup complete. If approval or application is still outstanding, report Wiki setup as pending and give **Wiki → Project setup** as the recovery path.
- Use project_open(path) to register/import an existing folder, project_export(project) for Git snapshots, project_forget(project) to remove only the panel entry. See scrum-project-storage for migration and explicit snapshot restoration. Do not delete project files as a substitute for forgetting.

Use compact tool results and avoid snapshots for project discovery.
