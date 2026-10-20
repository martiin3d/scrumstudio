# Scrum Studio

For an English product overview, workflow assessment, measured efficiency results and operational limits, read [Info.MD](Info.MD) or download the [PDF overview](output/pdf/Info.pdf).

The MCP and skills improve coordination and resumability, especially for longer or concurrent work. Small tasks still incur administrative calls. Synthetic tests demonstrate smaller responses; lower total provider quota consumption or faster real delivery has not yet been established. Costs and metrics remain optional and on-demand.

A local, English-language project workspace with a Kanban board, Daily notes, bullet-item Retrospectives, checkable Review notes and flow metrics. Each registered project stores its SQLite working data under its own `.scrum/local/` folder. No paid services, model calls, npm installation, or Python packages are required. Python 3.10+ is required.

## Start

Double-click **start.bat**, or run `python server.py`. Open http://127.0.0.1:8765. Use Project folders to create a project in an existing absolute folder or open/import a folder containing .scrum; then add tasks or notes. Click cards to edit details, priority, dependencies and status; drag cards between columns. New tasks start in TODO. The browser refreshes every 15 seconds while visible and on focus to pick up MCP changes. Close the console to stop the website. Data remains on disk. Set `SCRUM_PORT` to change the port, or `SCRUM_REGISTRY` to select another local registry (use the same value for web and MCP). `SCRUM_DB` without SCRUM_REGISTRY explicitly selects the old shared single-file compatibility mode; omit it for folder isolation.

## MCP connection

Task codes use the first three letters of the project name in uppercase and a project-local sequence starting at 1 (for example, Website: `WEB-1`, `WEB-2`). Spaces and punctuation are ignored; accents are normalized. Short names use their available letters; names without letters use `PRJ`. Existing tasks are numbered automatically in creation order per project. Codes appear on cards, in search, and in MCP results. The local numeric internal `id` remains the input for task updates and dependencies. Projects with the same first three letters share a prefix, so always include the project when referring to a code.

Tasks also support checklists. Open a task to add, edit, remove, or check items, then save. Cards show completed/total counts. Existing databases migrate automatically without losing tasks. The MCP accepts `checklist: [{"text":"Run relevant checks","done":false}]` on `task_create` and `task_update`; providing the field replaces the entire list, while omitting it preserves the existing list. Checklist completion does not automatically change task status. Limits: 100 items per task, 500 characters per item.

`mcp_server.py` implements MCP JSON-RPC over stdio and uses the same folder registry and project databases without requiring the web server. Copy the server entry from `mcp-config.example.json` into clients that support that JSON format. For Codex, add this to your user MCP configuration (adjust paths if moved):

```toml
[mcp_servers.scrum-studio]
command = "python"
args = ["/absolute/path/to/SCRUM/mcp_server.py"]
```

Restart/reconnect your MCP client after configuration. The example is supplied, not automatically installed into your global configuration.

Codex configuration reference: [official MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

Tools: `projects`, `project_create`, `project_open`, `project_export`, `project_restore`, `project_forget`, `project_migrate`, `project_chat`, `users`, `user_register`, `project_settings`, `session_resume`, `file_locks`, `next_work`, `task_get`, `task_create`, `task_update`, `task_claim`, `task_release`, `task_activity`, `task_changes`, `task_timing`, `project_metrics`, `note_add`, `note_check`, and `snapshot`. All project operations require the numeric `project` ID. `next_work` returns one candidate task and at most 20 blockers with a total count. `settings_seen` omits unchanged policy. Human notes and retrospectives are excluded. Priority is ascending (1 high, 2 normal, 3 low). Selection is actionable In Progress, then dependency-resolved Blocked without a prose blocker, then dependency-ready TODO. It respects agent WIP and known file conflicts. Prose blockers require agent assessment; the server never guesses that external conditions changed. Selection does not claim tasks; multiple local agents coordinate using atomic task and file claims.

## Skills

Repository skills live in `skills/`:

| Skill | Purpose |
| --- | --- |
| `scrum-project` | Create or select a project. |
| `scrum-task-create` | Create tasks/backlogs with priorities, checklists and dependencies. |
| `scrum-task-update` | Move tasks, edit priorities, manage blockers and check items. |
| `scrum-notes` | Write Daily, Retrospective and Review notes; check Review notes. |
| `scrum-work` | Execute the ongoing work loop with task continuity. |
| `scrum-close` | Close a session with accurate states and task handoffs. |
| `scrum-users` | Register identities, assign owners and manage task locks. |
| `scrum-blockers` | Manage multiple blockers and automatic resolution comments. |
| `scrum-activity` | Append work logs/comments or read paginated task history. |
| `scrum-theme` | Switch the website's browser-local dark/light appearance. |
| `scrum-settings` | Read or human-authorize changes to project WIP, workspace and evidence policy. |
| `scrum-file-locks` | Atomically reserve files, handle conflicts and release reservations. |
| `scrum-resume` | Review policy and old own file locks at every session start. |
| `scrum-unlock-all` | Bulk-release own files or human-requested project-wide reservations. |
| `scrum-complete` | Complete work with evidence, checklist validation and automatic cleanup. |
| `scrum-cancel` | Cancel/reopen work with an authored reason and automatic dependent unblocking. |
| `scrum-metrics` | Inspect project flow or task calendar time on demand. |

Copy selected skill folders with referenced resources into your agent's skill directory (for Codex, `%USERPROFILE%\.agents\skills`). Skills using shared Scrum policy link to `scrum-work/references/core-guidance.md`; keep that folder available. For structural code questions, use codebase-memory's graph workflow when installed, with coverage checks for relied-on files and direct source fallback for missing ranges. Setup recursively installs and verifies bundled files. Restart Codex if copied skills do not appear.

Example requests: `Use $scrum-project to create a project called Website Refresh`; `Use $scrum-task-create to add the agreed backlog`; `Use $scrum-task-update to move task 12 in project 3 to In Review`; `Use $scrum-notes to record today's blockers`. For autonomous work, provide the actual development goal and permitted working directory to `scrum-work`.

The MCP is a coordination service, not an autonomous worker. Skills instruct a connected AI to execute work, update statuses promptly and preserve task continuity for unfinished work. No retrospective is required. No model is called by this app, and idle time consumes no AI session tokens. Full snapshots are optional; concise task tools and cached versions reduce context usage. Historical notes remain available in the website and snapshot.

## Local scope

The server binds only to loopback and rejects cross-origin browser writes. Profiles identify local humans and agents; they are not authenticated accounts. Task locks coordinate trusted local workers, not hostile clients with database access. Network hosting and remote team authentication are not provided. For portable backups, use project_export; for complete local recovery, copy .scrum/local while web and MCP clients are stopped. The font uses an optional Google Fonts request with system fallback; core functionality works offline.

## Owners and work locks

Create a human profile with **+ User** in the header, then select it. Task cards show owner and active lock; the editor lets you assign an owner, claim/renew a lock, release it, or explicitly force release an abandoned lock as a human. Owner is a responsibility label, not an execution reservation: unlocked tasks may be claimed by another worker. New tasks start unassigned; assign them in the editor after creation.

Agents register with `user_register(identity, name, kind="agent")` once per logical worker. Use a persistent logical ID such as `agent:website:worker-1` and a readable display name. Reuse it across sequential sessions so the startup check can find previous reservations. Distinct simultaneous workers must have distinct identities. Reconnecting the same identity returns the same user; workers sharing the same display name remain distinct. Store the returned user ID as `actor`.

`task_claim(project,id,actor)` atomically acquires a lock, assigns owner, moves In Progress, and returns a private token. Only one concurrent claimant succeeds. Send actor+token on all edits to locked tasks. `task_update` renews active locks to one hour. Long-running work should renew before expiry via `task_claim(project,id,actor,token)`; lease_seconds defaults to 3600 and accepts 60..14400. Tokens are omitted from snapshots and work suggestions. Browser tokens are kept in tab session storage. A different browser tab must release/reclaim or perform explicit human recovery to edit a live lock.

Moving to TODO, Blocked, In Review or Done releases the task lock and all its file reservations but preserves owner. `task_release` explicitly releases task and files without changing status or owner. Released/expired In Progress tasks must be reclaimed before work; a human can return them to TODO for reassignment. Human profiles can force release an abandoned lock; coordinate with the current worker first. Former workers' tokens cannot edit a newly claimed task. There are no model calls or background AI heartbeats for locks.

Pass actor to `next_work` to exclude other workers' live locks. The result remains a suggestion; call task_claim before starting. No candidate may mean remaining work is currently locked. `users`, `user_register`, `task_claim`, and `task_release` are the four additional MCP tools. Update existing installed skills from this repository and reconnect the MCP after upgrading.

## Categories, blockers and activity

Tasks have an editable English category (General by default, max 80 characters). The board shows categories and filters by them. Search includes code, title, description and category.

The editor's **Blocked by** field supports multiple tasks selected by code/title. The API field remains `dependencies`, an array of internal numeric IDs (max 100, duplicates removed). They can be supplied on creation; new tasks still start TODO. Self-links, cycles and dependencies in other projects are rejected.

When a task first becomes Done, the server checks its dependents in the same transaction, removes completed task blockers, and appends a comment with the completing actor and timestamp. A Blocked task returns to **TODO** only when no task blockers or prose reason remains. It is not automatically claimed. Remaining blockers/prose reasons keep it Blocked. In Review does not resolve dependencies. Repeating Done without a state change does not duplicate comments. The response includes up to 20 affected tasks and their total count, avoiding another full-board query. Reopening a completed prerequisite does not recreate removed blockers; add any justified new blocker explicitly.

Open a task for **Activity & comments**: append a work-log milestone or an authored comment. Creation, changes, claims, releases, and automatic resolution also produce history entries. Entries store actor and UTC time; the browser renders local time. Comments from registered users are allowed during another worker's lock; work-log and task edits still require its owner/token. Profiles are local coordination identities, not verified login accounts. History is append-only; historical events before this upgrade are not reconstructed.

`task_get(project,id)` or `task_get(project,code)` retrieves one task without the board. `task_activity(project,id)` returns only the latest 20 events by default (max 50); use `before=oldest` for older pages or `after=latest` for new entries. To append, supply actor, text, kind (`work` or `comment`), and a claim token when required. Text is limited to 2000 characters. Combine status/category/checklist changes with `task_update(...,log="Implemented X; checks passed")` instead of making a separate logging call. Completing a task requires a registered actor for resolution attribution.

`task_update(...,claim=true,actor,...fields)` atomically edits and claims work, returning a private token. This avoids partial saves if an edited blocker or dependency prevents starting. Human profiles may return released/expired In Progress tasks to TODO for reassignment.

## Dark mode

The header's **Dark / Light** button switches theme and remembers it in the current browser. Before a choice, the system preference applies. Theme is local UI state and does not require a server or MCP call.

## Change review without model calls

`task_changes(project,id)` returns the uncommitted edits of a task's planned files straight from git: changed files with +/- counts, which of them were planned, an `unplanned` list for files touched outside the plan, and a unified diff (2 context lines) cut at `max_lines` (default 200, 20..2000) and ~12 KB with `truncated=true` and a hint to narrow with `files=[subset]`. `diff=false` returns only the file list; new files show their first 60 lines; `.scrum/` metadata is hidden. A fresh `task_claim` also reports `dirty_planned`, the planned files that already carried uncommitted edits, so a worker knows which changes are not its own before building on them. The `scrum-work` skill uses this for one before/after assessment per task (read the planned files before editing; review the diff once before `work_finish`), recorded in the same `log` of the finish call rather than in extra activity calls. The server never commits, reverts or judges the diff; the opinion is the agent's.

## Git automation per context group

Off by default. Settings (web **Settings → Git automation** or `project_settings`, human actor): `git_mode` Off|Branches, `git_commit` Task|Group, `git_push` Group|Task|Never, `git_base_branch` (empty = current HEAD), `git_branch_prefix` (`scrum/`), `git_remote` (`origin`). With Branches the server runs git in local code at coordination points, so no model turn is spent on it and Codex/Claude workers behave identically:

- **Claim** (`task_claim`, `work_start(claim=true)`, `task_update(claim=true)`): the checkout is moved to `prefix + <context group slug>`, or `prefix + <code>-<title slug>` for a task without a context group, creating the branch from the base branch when it does not exist. If the checkout holds uncommitted work (outside `.scrum/`) of a different branch the claim returns `claimed:false` with `branch_busy` (current branch, wanted branch, dirty files) and a comment on the task; no stash, reset or checkout is attempted. A lease renewal never switches branches. One checkout therefore works one branch at a time; concurrent agents share it when they share the context group.
- **Done** (`work_finish`, `task_update(status=Done)`, including a human on the board): `git_commit=Task` commits the task's planned files (`CODE: title`; everything outside `.scrum/` when the task has no plan) on the current branch; `Group` waits until the last task of the context group is Done/Cancelled and commits the whole checkout once. The push (`git push -u remote branch`, never forced, 60 s timeout) runs after the transaction per `git_push`: Group pushes when the context group completes (a task without a group pushes at its Done), Task after every Done, Never leaves it manual. The response carries `git` (branch, commit, push state) and `git_push` (outcome), and the task activity gets one `git:` comment per step, including `COMMIT FAILED`/`PUSH FAILED` with git's message. Failures never block or undo Done.

Requirements: git on PATH, a repository at the workspace root, a configured commit identity and credentials that work non-interactively for pushes (credential manager / SSH agent). Merging the branches back is a human decision; the server never merges, rebases, deletes branches or touches `main`.

## Token efficiency

Prefer `next_work` over snapshots and `task_get` for known codes. Pass `retro_seen` and `settings_seen`; unchanged retrospective/policy content is omitted. Task history and file-lock lists are excluded from routine work responses. Fetch only a relevant page if context is missing. Use a concise `log` within an existing update and record milestones rather than every tool call. Automatic dependency checks and comments run deterministically on the server; no model calls or AI polling are required. `exclude_ids` skips a recent conflict for one selection call; it does not alter task priority or state and should not become a permanent exclusion list.

## Project settings and completion policy

The **Settings** page and `project_settings(project)` expose:

| Setting | Default | Behavior |
| --- | --- | --- |
| `max_active_per_agent` | 1 | Range 1..20, per agent per project. Counts unexpired In Progress claims. Humans are exempt. |
| `workspace_root` | Registered project folder | Absolute directory for relative file paths; read-only in folder mode. |
| `require_evidence` | true | A transition to Done requires fresh evidence and a completed checklist. |
| `git_mode` … `git_remote` | Off | Server-side branch/commit/push per context group; see Git automation. |

Changes require an existing human profile as actor. Agents must not impersonate humans or weaken policy to bypass a limit. A human-requested change may be applied through the corresponding skill using that human's profile. Lowering WIP below existing live usage is rejected; no task is interrupted. Folder mode pins workspace_root to the registered location; move and reopen the folder to change it. Legacy mode rejects root changes while live locks exist. Renewing an existing task does not consume another slot. Expired task claims no longer consume WIP. Existing databases gain the defaults automatically; old records are preserved.

For Done, send `task_update(...,status="Done",evidence="Behavior test: expected output observed",...)` with the current actor/token and completed checklist. Evidence is limited to 4000 characters and is saved in verification history. Reopening clears the task's current evidence; a later Done needs a fresh explicit submission. The server checks presence/checklist consistency, not the truth of test results; it does not run project tests. The editor includes the evidence field, and dragging toward Done opens the editor for verification details.

## File locks and session startup

Open the actual project folder, then declare a minimal `files` plan on a task (max 100 file paths, 1024 characters per input). The **File locks** page shows live reservations, owners, tasks and expiry, with individual release and **Release all**. Reservations use exact canonical absolute paths, normalize Windows case/separators and resolve filesystem aliases where possible. They collide only inside the selected checkout. Different projects/clones do not detect each other even when they target the same physical file; use separate source folders. Paths outside the configured workspace and directory reservations are rejected. Hard-link aliases or edits by clients that ignore the MCP are not protected: these are cooperative reservations, not operating-system file locks.

Prefer `task_claim(project,id,actor,files=[...])` or `task_update(...,claim=true,files=[...])` to claim task and files in one transaction. A conflict returns `claimed:false` with compact conflicting reservations; no task claim, category edit or partial file acquisition occurs. `next_work` skips known planned-file conflicts. If the conflicting plan was not yet stored, supply `exclude_ids=[task_id]` to choose other work without retrying the same task. Do not create busywork when all useful work is locked or capacity is exhausted.

For newly discovered files during an active task, call `file_locks(project,action="acquire",id,actor,token,paths=[...])` before editing. The batch is all-or-nothing; `acquired:false` reports conflicts. Work-log and task/claim renewals extend still-valid reservations with the task lease. Stop editing if ownership or renewal fails. Expired reservations no longer block others; a fresh claim does not adopt another generation's private token.

At **every new session**, the `scrum-resume` skill calls `session_resume(project,actor)` once. It combines policy, current active count and your own live reservations within this project checkout. Review these and release obsolete own IDs before editing. Needed reservations remain held. Resume/list results default to 20 entries (max 100), with `after=next_cursor` pagination when needed; no constant polling is required. The server does not infer which old files remain useful: the skill makes that decision from the intended work. This startup policy is an agent instruction, not an OS-level restriction on other programs.

`file_locks(project,action="release",actor,ids=[...])` releases selected own files without needing a lost previous-session task token. With `all:true`, it releases all that actor's reservations in the selected project in one call. It does not release the task claim or change task status. Human profiles may use `force:true` for project-wide cleanup or another worker's IDs; agent profiles cannot. The web's Release all dialog makes this scope explicit. Global means the selected project here, not every project in the database. Stop/coordinate file edits before a bulk recovery; subsequent editing needs fresh acquisition.

## State times, cancellation and metrics

Each task now shows **Time in each state**, summing repeated visits, plus a paginated structured visit history. State changes record entered/exited timestamps and actor automatically, including claims and dependency-triggered returns to TODO. Metadata edits, renewals, comments and explicit lock release do not reset the state clock. Durations are calendar time including waiting, not agent CPU time or token usage. Current Done/Cancelled residence also accumulates; it is not included in cycle/lead work duration after completion.

Existing tasks receive a baseline when this version first opens the database. Earlier time is explicitly unknown and partial_history is true; old text comments are not treated as complete timing evidence. New tasks are tracked from creation. `task_timing(project,id)` returns compact totals; history=true adds the latest 20 state visits (max50), with before=oldest pagination. These records are fetched only on demand, not included in every next_work response.

**Cancelled** is a terminal work state requiring actor and a nonempty cancel_reason (max2000 characters). It releases task/files and remains visible in the board/history. It does not require completion evidence or count as Done. The server removes the cancelled task from dependent blocker lists and records authored resolution comments. Blocked tasks return to TODO only when no task dependencies or prose reason remains; remaining blockers are preserved. New links to cancelled prerequisites are rejected. Reopen through TODO before claiming again. The current reason clears on reopening unless explicitly supplied; the original reason remains in history.

The **Metrics** page provides current task distribution, open-task count, distinct completed tasks, average cycle/lead time with sample counts, blocked-time share, observed time per state, and a daily completion/cancellation flow chart. Web presets cover 7, 30 or 90 UTC dates including today. The chart includes an accessible daily data table. No chart libraries, paid analytics, model calls or extra dependencies are used.

`project_metrics(project,days=30)` returns summary only; include_series=true adds daily chart points. days accepts 1..365. Current counts are distinct from period flow. Done transitions count every re-completion; completed_tasks counts distinct task IDs with a Done transition in the period. Lead time runs from creation to the latest Done in the period, and cycle time from first In Progress to that Done. Legacy baseline tasks are excluded from lead/cycle averages. Observed state averages include partial visits clipped to the selected period and must not be confused with complete lifecycle averages.

## Install into Codex

From this repository run `setup.bat` on Windows or `bash setup.command` on macOS. The legacy PowerShell installer `./scripts/install-codex.ps1` also works and defaults to the current user's profile. Setup copies/verifies bundled skills, backs up existing matching skills and config, registers the local stdio server, and checks a real initialize/tools-list/projects exchange. Other configured MCP servers are retained. Python and Codex CLI must be on PATH. Backups go under `.codex/backups/scrum-studio/`. Reopen the chat to load updated tools and restart the website after backend upgrades.

`python scripts/check_mcp.py` separately checks the transport without printing project names or secrets. It reads the configured local app database and applies pending schema migrations, but creates no projects, users or tasks.

## Verification

Run `python -m unittest discover -s tests -v`. Tests use temporary databases and exercise project isolation, legacy migrations, priority selection, identity/task/file races, WIP limits, evidence/checklist validation, canonical conflicts within a checkout and isolation across overlapping projects, all-or-nothing reservations, own/project bulk release, session cleanup and policy caching, categories, automatic dependency comments, authored/paginated history, review toggles, web request handlers and MCP tool flow. `node --check public/app.js` checks browser script syntax.
# Portable setup and project chat

Requires Python 3.10+ on Windows or macOS. The website has no external Python packages and does not require Codex. Installing the MCP requires the Codex CLI in PATH.

- Windows: run `setup.bat` once to install bundled skills and register the MCP; run `start.bat` for the website.
- macOS: run `bash setup.command` once, then `bash start.command`. Alternatively `bash setup.sh` and `bash start.sh`. To enable Finder double-click, run `chmod +x setup.command start.command` locally. Files downloaded from the internet may require normal macOS approval before opening.
- `python scripts/setup.py --skills-only` installs skills without MCP registration. `python server.py` starts only the website. macOS uses `python3`.

Setup resolves the current interpreter, repository and home paths, backs up existing matching skills and Codex config, and probes the registered stdio server. Run setup again after moving the checkout. It does not enable login startup. Close the terminal or press Ctrl+C to stop the website.

If a desktop MCP connection is slow, compare it with the same server over direct stdio before changing storage settings. From this checkout, PowerShell can run `'{}' | python scripts/mcp_client.py --tool projects --timeout 15`. From another project use the absolute script path and the configured Python interpreter. For larger or sensitive arguments use stdin or `--args-file` pointing to a private ignored UTF-8 JSON file; never put claim tokens on the command line. The CLI prints the decoded tool result to stdout and transport timing to stderr, with a nonzero exit on tool or transport failure. It uses the existing MCP server, registry, quotas and reservations; it does not access coordination databases directly. There are no automatic retries. After an uncertain write, reconcile current tasks/claims before trying again, since the write may already have committed. Each RPC has a finite timeout (15 seconds by default), followed by bounded child cleanup. This is a transport fallback, not a repair of the desktop connection. Use `python scripts/check_mcp.py --project-path /absolute/project --timeout 15` for a bounded handshake/tool probe with timings, registration matching and structured errors. A successful listing does not test write access. Use `coordination_metrics` for server processing time, which excludes client/model waiting.

Storage failures include a stable error code, recovery step and diagnostic reference in MCP structured content or the web response's `diagnostic` field. `STORAGE_PERMISSION_DENIED` requires access to the configured registry and selected project's `.scrum` folder through the normal approval mechanism; a direct CLI inherits its shell sandbox. `STORAGE_BUSY` means an operation holds a lock: let it finish and reconcile before retrying. `STORAGE_CONSTRAINT` requires checking the project, actor and task identifiers; `STORAGE_UNAVAILABLE` covers other storage access failures. Local `data/diagnostics.log` rotates at 1 MB with one backup and stores only correlation IDs, error classes/codes and source frame metadata, never request bodies, tokens or raw exception messages. If it cannot be written, the response points to server stderr. Diagnostics and measurement failures never replace a committed result.

Keep permission-required operations sequential so a new prompt does not supersede an outstanding approval. These messages come from the host approval interface, not SQLite. Unity `PROJECT_REGISTRY_UNREADABLE`/`LockUnavailable` and package-host DNS failures belong to Unity or networking; verify the resulting Unity project before retrying creation. Scrum cannot repair Unity's registry or grant filesystem permissions. Updated bundled startup/work skills teach these recovery steps; install them using `python scripts/setup.py --skills-only --client codex` (backs up matching installed skills) and reopen the client chat to load them.

The **Chat** section and `project_chat` MCP tool share an append-only project conversation, with author, UTC timestamp, optional recipient and project task. Recipients are routing hints, not private messages. Reads default to latest20, max50; `after=latest` reads incrementally and `before=oldest` loads older messages. Follow `has_more` without skipping pages. No chat data is added to routine snapshots or next_work. The web chat uses manual refresh; no automatic agent polling, notifications or wakeups. Use `scrum-chat` for authorized coordination; messages never grant execution authority or bypass locks. Persist decisions and verification on tasks.

Folder storage, portable snapshots and project-isolated file reservations are implemented; existing shared databases need explicit project_migrate before using this mode. Local clones do not coordinate locks or chat across computers; that needs a shared server. macOS launchers require validation on a real Mac; Windows tests do not establish native macOS execution.



## Folder storage and Git workflow

```text
<project>/.scrum/
  .gitignore            # excludes /local/
  project.json          # schema_version, stable project UUID, name and description
  snapshot.json         # deterministic portable data
  local/                # never version this folder
    scrum.db            # working records and local task claims
    locks.db            # advisory file reservations
    checkout.json       # local checkout UUID, distinct from portable project UUID
    backup-*.db         # backups made before explicit restoration
```

The shared website remains one local panel. **Project folders** supports Create in folder, Open / import folder, Export for Git, Restore snapshot and Remove from panel. Enter an existing absolute directory on the machine running the local server; browsers cannot provide its full filesystem path through a standard upload picker. Removing a panel entry never deletes the project files. Missing/moved folders are shown as unavailable and can be forgotten, then reopened at their new location.

`data/registry.db` contains local numeric IDs, project UUIDs, paths and checkout UUIDs only. Human/agent profiles live separately in local `data/identities.db`; referenced authors are also saved portably with each project's snapshot. Both website and stdio MCP resolve the same registry. The website can be stopped while agents use the MCP. Each project's tasks, notes, chat, activity, state timings and policy live in its own database; file reservations live in an attached database so task+file claims remain atomic.

- Create: `project_create(name, path, description?)`; open/import: `project_open(path)`.
- Export: `project_export(project)` writes JSON and returns only counts and the snapshot path. It does not push or stage Git changes. Version `.scrum/project.json`, `.scrum/snapshot.json` and `.scrum/.gitignore` with your source code.
- Clone: open its folder. Without a local database, the snapshot is validated and imported. It keeps the project UUID and task codes, assigns a new checkout UUID and resolves authors by their stable identity strings.
- Open an existing local database: preserves local work; never silently replaces it with snapshot.json after Git updates.
- Restore a changed Git snapshot: release active claims, then explicitly request `project_restore(project, confirm=true)`. A local backup is created before replacement. Invalid data rolls back; nothing restores live locks or session tokens. There is no automatic merge. Resolve Git JSON conflicts first; duplicate task numbers, UUIDs, broken relationships and dependency cycles are rejected.
- Forget: `project_forget(project)` removes only the registry entry. Reopening may assign a different numeric registry ID.

Snapshot records have stable UUIDs and relationships reference UUIDs/identity strings instead of machine-local IDs. Task codes remain project-scoped. After import/restore, resolve tasks by code again and discard cached numeric IDs, note IDs, pagination cursors and tokens. Identical unchanged project data exports byte-identical JSON; export adds no timestamp. Snapshots exclude managed leases, private tokens, file reservations, checkout IDs and local workspace settings; file plans become relative. User-written descriptions, comments and logs remain intact and may contain paths or sensitive text, so review them before publishing.

### Existing shared database migration

Stop other old website/MCP processes, then call `project_migrate(project, path)` through a newly started MCP process. The destination must be an existing folder without .scrum. Migration stages and validates a full copy before exposing it, creates `data/scrum-backup-*.db`, preserves existing task/history IDs and local leases, registers the folder and creates its initial portable snapshot. The source database remains untouched, with a local migration marker preventing it from appearing as an active duplicate. Remaining unmigrated projects stay visible in Project folders. Restart old clients after migration; old loaded Python processes cannot reload the new router automatically and must not continue writing to the old source.

Use the `scrum-project-storage` skill for these operations. This works on Windows and macOS with Python 3.10+. Filesystem locks remain cooperative; separate computers/clones do not coordinate live work through Git. macOS native execution still needs testing on a Mac.


## Agent rules, usage limits and controls

Project Settings includes Task sizing (Small by default; Balanced / Unrestricted), 5-hour quota stop (92% consumed), weekly stop (94% consumed), usage guard and check interval (60s default). Only a human profile can change policy. Small is a semantic skill rule: one independently verifiable outcome, splitting independent results without trivial tasks or speculative backlogs.

Before guarded folder work, agents call agent_checkpoint with actual consumed quota percentages. Safe editing boundaries check Stop and quotas, pausing on unknown/reset/stale data. At either threshold, the call returns the actor's claimed work to TODO, clears own leases/files, and saves authored activity, Review, Daily, Retrospective and chat. No completion is fabricated. New claims and In Progress edits are rejected until safe again. Explicit legacy SCRUM_DB clients without checkpoints retain compatibility, so migrate them and update their skills for enforcement.

Dashboard counters show recent active agents and tasks. A green task dot requires recent cooperative heartbeat plus a valid claim, not just In Progress. This is reported activity, not OS process detection. Runtime sessions, quota observations and control signals remain local in locks.db and are never exported/restored with snapshots. Policy is portable; active workers are not.

**These are cooperative limits, not provider hard caps.** Usage can advance between provider updates/checkpoints and during graceful shutdown. Defaults reserve headroom but cannot guarantee the exact final percentage. Desktop clients must cooperate; the optional bridge interrupts only CLI workers it owns. Unknown values are never represented as zero.

### Codex and Claude Code setup

Windows: setup.bat --client codex (default), --client claude or --client both. macOS: bash setup.command --client CLIENT. Selected CLIs must already be installed/signed in. --skills-only copies skills without registering MCP. Codex skills go to ~/.agents/skills; Claude skills to ~/.claude/skills. Existing matching skills/config are backed up. If Claude reports a duplicate MCP registration, inspect its backed-up configuration and explicitly remove/replace that registration before rerunning setup. Setup does not launch workers or overwrite Claude status-line settings. Setup registers the MCP with the compact worker catalog (`--profile worker`: the task workflow only, short descriptions, about 60% fewer tool-definition tokens per turn); use `--full-tools` for the full administrative catalog, which the website also covers. Rerunning setup replaces an existing `scrum-studio` registration. Claude bridge workers get the same worker catalog through a generated `data/claude-worker-mcp.json`, `--strict-mcp-config` and an explicit tool allowlist. Wakeup runs (Codex and Claude) are pre-claimed by the supervisor: it calls `work_start(id, claim=true)` in local code and passes the full task and token in the prompt, so the worker spends no turns on startup and finishes with one `work_finish` (`claim_next=true` chains the next task in the same call). Optional Claude Code hooks (`setup --client claude --claude-hooks`) add board context at session start, quota checkpoints at each response end, a Stop notice on prompts and an offline handoff at session end, all without model turns; folders without a board and bridge workers are skipped. Existing `~/.claude/settings.json` entries are preserved and backed up.

If a Windows worker cannot read an installed legacy skill under `CODEX_HOME/skills`, run `python scripts/setup.py --skills-only --client codex --repair-skill codebase-memory` (replace the name as needed). This explicitly relocates that skill with all resources into `~/.agents/skills`, verifies content, and archives the legacy directory under the printed backup so new workers discover one canonical copy. Conflicting canonical content or links stop repair without overwriting either skill. Restart only the affected idle worker after setup; running chats cache skill locations. No skill download, global permission change, or Python modification is required.

Codex desktop can expose get_usage_limits. The local adapter reads [account/rateLimits/read](https://learn.chatgpt.com/docs/app-server) from codex app-server without a model request, matching duration300 /10080 minutes and conservatively taking the highest relevant USED percentage. Missing dimensions stay unknown.

Claude Code publishes five_hour /seven_day usage in its [status-line payload](https://code.claude.com/docs/en/statusline). Configure this optional command as a statusLine command, or incorporate it into an existing wrapper:

```text
python /absolute/path/to/SCRUM/scripts/claude_usage.py --output /absolute/project/.scrum/local/claude-usage.json
```

It consumes stdin JSON and saves only numeric percentages, reset times and observed_at, without credentials/transcripts. The feed must actually update while workers run. A missing/stale feed pauses guarded work; noninteractive versions that do not emit status-line data need another truthful live feed. Context-window usage is not a substitute. The Claude Windows app is installed, but its shared account must not be opened or used. Claude integration remains available in the repository; it was not exercised against that account.

### Wakeup / Stop and optional CLI bridge

Before starting a model, each bridge calls `worker_launch` in local code. Empty, blocked, quota-held, capacity-limited or file-conflicted work does not start inference. Atomic ten-minute advisory startup offers prevent multiple bridges from launching for the same task or overlapping planned files; independent tasks can launch concurrently. An offer is not a task/file claim: workers still review the retrospective and explicitly claim ownership. Offers release on completion, failure, Stop or disconnect, and expire after crashes. Skipped starts are recorded in Chat. Idle supervision never polls the model; a new authorized Start/Wakeup or eligible opted-in quota reset is required to retry.

Codex workers and quota reads share executable discovery: `SCRUM_CODEX_EXECUTABLE` can select an existing executable; otherwise the installed Desktop runtime is preferred over PATH. This avoids an older global CLI failing on a newer configured model/catalog. Discovery never installs tooling or changes the selected model. Failed runs show their exit code and a bounded provider error in Agents, readiness and Chat. Wakeup instructs each worker to claim eligible pending tasks, implement and verify them, and continue until available work is exhausted or a real policy/blocker stops it.

Managed Codex keeps `workspace-write` and uses `on-request` approval with the automatic reviewer, so approval-required MCP calls can be reviewed instead of immediately rejected by exec's noninteractive `never` default. Managed restrictions and reviewer denials still apply. The prompt includes the supervisor's absolute Python path for quota and bounded stdio access when the worker has no Python on PATH.

Web controls call project_control with the selected human profile. Stop immediately blocks new agent work; agents stop editing at their next safe checkpoint. Wakeup restores the previously enabled managed-agent set with saved identities, roles and clients, including disconnected bridges. If no profiles or connected manual bridges exist, it creates/reuses one default Codex agent using the CLI's configured default model. Individual Stop removes a profile from that remembered set; Start adds it again. Prepared profiles with immediate start disabled are not automatically enabled. Claude restoration requires explicit configured-account authorization via confirm_client_account=true or the profile's Start confirmation. Wakeup never reopens arbitrary Desktop chats. To use a manually connected bridge instead:

The control bar shows readiness, launch requests/errors and a **Manage agents** shortcut. Restored managed bridges receive an immediate-start request, so a second Wakeup click is unnecessary. Existing connected bridges receive the signal without a duplicate bridge. Fresh quota, WIP and file reservations still gate model execution. `project_control(action=wakeup)` returns `signal_recorded`, `launches` and `wakeup` recipient counts/reasons/next_step. A response marked starting proves only a requested bridge launch, not a running provider model; actual instructions and results appear in project Chat. Pass start_agents=false for signal-only behavior (also retained for legacy single-file projects). This flag never starts or creates profiles. Repeated/concurrent initial requests reuse the same default profile; partial launch failures are reported individually and do not undo successful starts.

```text
python scripts/agent_bridge.py --project 1 --client codex --worker worker1 --allow-run
python scripts/agent_bridge.py --project 1 --client claude --worker worker2 --allow-run --usage-file /absolute/project/.scrum/local/claude-usage.json
```

Then click Wakeup. Manual bridge startup ignores old signals; each bridge uses its own persistent identity. Use distinct worker names for concurrent workers. Python polls local MCP state every2s outside the model and checks usage at the configured interval. It logs under .scrum/local, preserves CLI permission checks, and interrupts only its own process group on Stop/quota. Handoff occurs after process exit, so locks are not freed while that worker edits. Unrelated desktop sessions stay cooperative. Opening the web alone never launches a model; a new Wakeup is required for a manually connected bridge, while managed Wakeup restoration requests immediate execution after quota checks. CLI sessions consume normal provider usage when launched. No OS login service is installed.

Read usage without launching a worker/model: python scripts/agent_bridge.py --project 1 --client codex --usage-once. Missing telemetry produces {}, not a fabricated zero. New skills: scrum-rules, scrum-usage and scrum-control. Common MCP tools support both clients.

The usage_guard checkbox controls whether missing/stale usage blocks work; a known fresh percentage at its threshold still stops work even if that checkbox is cleared. Codex app-server quota reads run off the bridge control loop, so a slow quota fetch does not block local Stop signals.

New tasks require a nonempty description and a separate acceptance_criteria array in the initial atomic request (web and MCP). Incomplete creation does not consume a task number. Existing tasks/snapshots remain readable without invented criteria. Progress goes in Updates & comments or authored task_activity/log; edit the specification only when the intended scope or acceptance changes.

## User profile removal

The Users page supports Delete and Restore (Show deleted profiles). user_manage(actor,id,action=delete|restore) requires an active human and applies across registered projects. Deletion disables acting/new assignments while retaining names/identity links for historical task activity, chat and ownership. It cannot remove the last active human or a profile with live claims/files/recent running sessions. Restore is explicit: registering the same deleted identity cannot reactivate it. This is local profile management, not deletion of Codex/Claude accounts. Deleted state is local to the installation; importing history must not reactivate a previously deleted local author.

Local account constraint: Claude Windows is installed, but its separate shared account must not be opened or used for this project work. No Claude runtime/account validation was performed; client compatibility is maintained as source code only.

## Compact agent workflow and coordination cost

Use work_start(project,client,actor or identity+name,settings_seen?) once per session. It combines identity reuse, old-lock review, quota check, changed settings and one candidate. Review own reservations before task_claim(...,files=[...]), which already reserves task and files atomically. work_finish(...,evidence,checklist?,settings_seen?) completes and returns next work without another call. Completion commits before selection; next_error does not undo Done. No next task is automatically claimed. Compact output omits empty fields and repeated instruction text; verbose=true is available for diagnostics. Existing low-level tools remain supported.

Optional quota-only monitor (no model/worker): python scripts/usage_monitor.py --project 1 --actor 2 --client codex. Add --once for a bounded check. It writes ignored checkout-local telemetry consumed by work_start/agent_checkpoint; stale timestamps stay stale. Claude requires an existing --usage-file and is never invoked by this utility. If the CLI cannot supply telemetry, use the desktop quota tool; no fabricated fallback or guard bypass. The monitor does not force-stop desktop sessions: safe-boundary checkpoints are still required. No background service is installed automatically.

Metrics now includes coordination_metrics: local lifetime MCP call counts/errors, request/response UTF-8 bytes and server milliseconds per tool. Aggregates contain no request/response content, are excluded from Git snapshots and do not include web requests, model execution or history before instrumentation. These are transport measurements, not provider token counts or evidence of savings without a comparable baseline.

## Lightweight single-agent operation

Settings exposes workflow_mode: Auto (default), Light or Standard. Project status shows configured/effective mode and the eligibility reason; this is current eligibility, not OS process detection. Auto and Light prefer the same safe shortcut: Small task with no dependencies/blockers and one worker/claim. Unknown scope uses a conservative limit of five planned files/checklist/acceptance items and 4000 description characters; an explicit Small estimate with a reason can exceed those numeric heuristics. Large scope always uses Standard. Standard is the fallback and can be explicitly selected. Empty projects report No actionable task. Existing snapshots without workflow_mode restore as Auto.

For a known authorized task, work_start(...,id,claim=true,files) can claim and reserve files in the startup call after policy/quota checks. Old own locks defer claiming for review. Selection is never blindly claimed; conflicts and concurrency are checked again inside the claim transaction. The response contains task, claim token and workflow status. Never edit without claim.claimed=true. Unknown scope, changed task scope or required quota checks can add calls.

At final delivery, work_finish(...,evidence,close_session=true,client,retrospective_items?,review_items?) completes, frees locks, saves only explicitly supplied optional task-linked notes and marks the worker offline. It does not fetch next work. Other own unfinished work requires handoff first. Completion commits before notes/closure: inspect close_error and returned note IDs for partial failure, and repair only the missing steps. Routine updates are meaningful milestones only; no per-step reporting requirement. With cached policy and fresh quotas, a known small fix can use two coordination calls. This reduces coordination, not a measured provider-token savings guarantee.

## Context and measurement efficiency

Tasks have optional continuity (max2000 characters): current result, relevant files, verification and next step. task_update/work_finish can save it in an existing call with authored activity. It is portable; old snapshots default to empty. A continuity_stale flag means task context changed since writing it. Never treat stale summaries as verified current state. The web task editor displays the summary and stale warning.

Task reads return version. Send version_seen to task_get or task_seen to next_work/work_start/work_finish: unchanged tasks return identity/version and changed=false; retain the cached fields. Versions cover task data and live-lock state, not separate comment history. Policy still uses settings_seen; retro_seen remains accepted for old clients but no longer loads human context. Task search accepts query, code, status, file, limit (default20,max50) and after cursor; returns concise matches without history. Literal wildcard characters are escaped.

scope_size=Unknown|Small|Large plus scope_reason (required for Small/Large,max500) express expected complexity. A justified Small scope overrides size heuristics, but dependencies, parallel work, quota/Stop, WIP, file reservations and evidence gates remain. Unknown retains conservative heuristics; Large uses Standard. The task editor exposes this estimate.

work_start returns measurement_session; pass it on subsequent project MCP calls to attribute cost across stdio reconnects. coordination_metrics supports filter_session and task. Metrics shows latest20 sessions and top50 tasks. No arbitrary input/content is logged; untagged calls are unattributed. Start mode is initial eligibility, not a complete trace of effective modes. Serialized JSON bytes and server milliseconds do not measure provider tokens or prove savings. Scoped metrics begin at this upgrade; lifetime totals may include earlier calls.

The routine skill entrypoints now share a brief execution guide and load specialized recovery/admin skills only when relevant. In this repository, python scripts/verify.py --files PATH... plans targeted checks; add --run to execute once. Core/unmapped changes use full regressions, bounded changes use focused suites; web flows and skill references still need appropriate review. Passing unchanged checks need no repetition without new scope, failure or unresolved risk.

### Estimated versus reported task cost

Tasks optionally store estimated_tokens and estimated_quota_percent (five-hour account-window estimate). The web task editor compares estimates against client-reported input+output tokens; missing usage stays Not available. Reports are cumulative per persistent agent identity and task-specific run_id, with a required source. Retries replace snapshots without double counting; independent runs accumulate. Only report telemetry attributable to this task, including earlier attempts; do not copy an entire chat total across tasks. Cached input is already part of input_tokens. work_finish can submit usage_report atomically with completion; task_update can record it on cancellation or a later telemetry arrival. Reports are retained in portable snapshots. Coverage may be partial; no claim of complete billing is made.

Account quota readings remain on Agents, explicitly account-wide: never sum duplicate readings or assign their deltas to individual tasks. Estimation is optional, without automatic model calls or new polling. Codex/Claude integrations supply task-attributable telemetry when available; desktop clients that expose no such telemetry remain unavailable.

### Routine coordination efficiency (2.7.0)

Task reads, next candidates and board snapshots omit cost by default. task_get(include_cost=true) opts into estimates and reported totals; the web fetches this only when opening a task editor. Versions are view-specific: a non-cost version cannot suppress a requested cost response. Exports still preserve complete portable cost data.

Compact work_start/work_finish omit zero diagnostic counters, repeated active counts and unpaused control details; stop signals, nonzero conflict/configuration counts, policy changes, pending own locks and own-lock pagination remain available. next.no_candidate explicitly indicates no selected task, not an empty backlog. verbose=true retains full diagnostics. Cost and other specialist guidance is loaded only when needed. Retrospective is optional for a new lesson; Daily/chat/Review are not required for each small task. Automatic pause handoffs must not be duplicated.

Run python scripts/benchmark_coordination.py for isolated, equal-input synthetic response comparisons. Before/after this change: Light compact startup 1112 -> 678 bytes (-39.0%); Standard 1130 -> 696 (-38.4%); default task 752 -> 502 (-33.2%); opt-in cost task remains 752. Fixture uses a bounded task, a reported-token record, cached settings and no automatic claim; byte counts use compact UTF-8 JSON. These are payload measurements, not a workload benchmark, token count or evidence of provider quota savings. Live comparisons should use existing measurement_session and coordination_metrics after several equivalent tasks, keeping model, context, provider and telemetry coverage comparable. Do not add per-task polling or automatic reports for measurement.

The four routine skill entrypoints changed from 6875 to 4493 UTF-8 bytes (34.6% shorter); bytes are not tokenizer counts.

### Plan first, execute second

Scrum execution skills require the entire currently known authorized scope to be registered or matched to existing TODO tasks before the first claim. Initial writes contain description and acceptance criteria. Prefer independently verifiable outcomes, explain cohesive grouping briefly, and use checklist steps within a single deliverable. Measurement can be acceptance evidence or a separate bounded deliverable. Register justified discoveries before switching; avoid speculative tasks, duplicate work and retroactive planning. A single bounded request can use one task. Backlog-only requests never start implementation. The canonical portable rule is in skills/scrum-rules/SKILL.md; scrum-work and scrum-task-create explicitly route to it, and this repository repeats the essential requirement in AGENTS.md. This is an agent instruction: the MCP cannot know every deliverable in a human request and does not mechanically enforce completeness.

## Sharing this application through Git

This repository distributes the website (`public/` and `server.py`), Python MCP/backend, `skills/`, `scripts/`, tests, documentation and Windows/macOS launchers. Its development board is local: the root `.gitignore` excludes the entire `.scrum/` directory (including project.json and snapshot.json), `data/` (registries, users, databases and backups), logs, Python caches/virtual environments, local environment files and local Codex/Claude configuration directories. Keep `mcp-config.example.json` and the source `skills/` directory in Git.

A clean clone starts without the developer's board, users, task history, file locks or folder registry. Run the platform setup/start launcher, then create a project or open an existing project folder in Project folders; configure MCP using your own checkout path. The board remains available locally to its original owner; ignoring files does not delete them.

Other repositories may intentionally version their portable `.scrum/project.json` and `.scrum/snapshot.json`; that is a separate project-data sharing choice. This application's Git distribution deliberately excludes both. If board or machine-local files were previously committed, adding ignore rules does not remove them from the Git index or existing history; review them before publishing.

## Stop the website

Windows: double-click **stop.bat**. macOS: run **bash stop.command** or **bash stop.sh** (or double-click stop.command after granting executable permission with chmod +x stop.command). These stop only this checkout's website, using a random local control token in ignored data/web-PORT.json and an authenticated loopback request. They do not stop MCP, agent workers or modify boards. For a custom port use the same SCRUM_PORT value used at startup. An already stopped instance with no control file is a no-op; stale or rejected shutdown reports an error and never kills a process by PID/name/port. A website started with an older version needs one manual restart to register its control record. Existing Ctrl+C/console-close behavior is still available. On Windows errors pause the launcher so the message can be read.

## Wakeup as a CLI continuation message

A connected bridge turns a new Wakeup signal into a continuation instruction for its configured Codex or Claude CLI. The project Chat records a concise launch reference under the worker identity, then its final response (bounded to 1,500 characters) and exit status. Stop/quota interruptions, held Wakeup and launch failures are visible there too. Tool/reasoning streams are not copied to Chat; full local logs remain in the project's ignored .scrum/local/worker-CLIENT-NAME.log. Each Wakeup starts a fresh CLI conversation and recovers progress through Scrum task context, not by injecting into or resuming a Desktop chat. Final-response availability depends on the CLI emitting its supported JSON events; absence is reported honestly. Existing quotas, locks and permissions still apply; chat visibility failures do not interrupt worker cleanup.

Connect explicitly from this installation, substituting the local project ID:

```sh
python scripts/agent_bridge.py --project ID --client codex --worker studio --allow-run
python scripts/agent_bridge.py --project ID --client claude --worker studio --allow-run --usage-file /absolute/path/to/claude-usage.json
```

Leave the bridge running and then click Wakeup. Connection never consumes an earlier Wakeup or starts a model immediately. Claude requires the CLI, separately authorized account/configuration, and a fresh usage feed from the documented status-line adapter while the usage guard is enabled. Do not launch a shared account without authorization. No Desktop synchronization or native chat visibility is promised.

Codex documents custom-client thread/resume and turn/start through its own App Server, but this installation has no documented connection to the live Desktop conversation. The portable bridge uses supported non-interactive CLI entrypoints instead: [Codex](https://learn.chatgpt.com/docs/non-interactive-mode), [Claude Code](https://code.claude.com/docs/en/headless). This research does not establish that all Desktop integration is impossible; it establishes the boundary of the supported integration implemented here.

Stop handoff: the bridge confirms its CLI has exited before cleanup. Each claimed task returns to TODO with one bounded continuity summary of recorded progress and the next step. Checklist, acceptance, planned files and evidence remain in their task fields. Own task/file locks are released; concise Chat/activity references locate that continuity without copying it. No automatic Daily or Retrospective is written. Captured final text remains human Chat content and is never copied to task continuity; never invent missing progress. Existing records are reused without another model call. Stop does not mark work Done. Repeated pause without claims adds no notes.

## Named agents from the website or MCP (2.8.0)

Open **Agents > Add agent**, choose a name and client, then either import a UTF-8 `.md` or `.toml` role file, type instructions, or leave instructions blank for the standard Scrum agent. Markdown/TOML content is loaded verbatim into an editable role text field, not applied as executable client or model configuration; maximum 8,000 characters (import file up to 32 KB). **Start immediately after creation** is checked by default and authorizes launching that configured CLI. Uncheck it to save a profile without running a model. The agent receives a persistent identity and the normal Scrum task, WIP, file-lock, Stop and quota workflow.

The site starts a dedicated bridge without requiring a terminal command. Initial execution waits for fresh usage telemetry; project Stop, unknown usage or reached thresholds still hold work. Opening Agents alone never starts a worker. Profile cards show bridge status and launch errors; **Start / continue** requests an individual run, and **Stop agent** disables only that profile and initiates its normal interruption/handoff cleanup. Project Wakeup also restores disconnected profiles in the remembered enabled set; it preserves instructions/client/actor, honors individual Stop and keeps failed-start intent recoverable. Waiting for a stopped bridge to disconnect avoids overlapping copies. Do not treat a starting/connected bridge as proof of an active model; task activity still depends on heartbeat and claim.

Claude requires its installed CLI, an explicit account-use confirmation and an absolute usage JSON feed path before launch. The application does not sign in or switch accounts, bypass CLI permissions or make a shared account available automatically. Codex is the default. Role text supplies task preferences, not permission to override process safeguards or run arbitrary command templates.

MCP exposes `agent_manage(project,action=list|get|create|start|stop)`. Mutations require an active human actor. Creation accepts `name`, `client`, optional `instructions`, `source` (Markdown/TOML filename), `usage_file`, `start_immediately` and `confirm_client_account`. Example to prepare an agent without consuming model usage:

```json
{"project": 1, "action": "create", "actor": 1, "name": "Frontend reviewer", "client": "codex", "instructions": "Focus on accessibility and small UI regressions.", "start_immediately": false}
```

Resolve local project and human IDs before using that illustrative example. `get(id,include_instructions=true)` opts into full instructions; compact lists omit them. `launch_failed` can return an existing newly created profile plus an error, so fix it and start that profile instead of retrying creation. A connected waiting bridge receives an individual generation signal; it does not spawn duplicate processes. Profile control polling is local and invokes no model.

Profiles live in `.scrum/local/agent-profiles.db`; bridge logs also stay local. They are not exported or shared through Git, and cloning/importing a project does not launch profiles from another machine. Profile roles and names are configuration, while ordinary task assignments use their registered agent identity. No real provider run is needed to test profile management: automated regressions mock CLI processes. Native macOS/provider execution requires separate validation.

Current release: MCP **2.9.0**, **40 tools** (19 in the worker catalog), **24 bundled skills**, including `scrum-agents`. Earlier Info documents retain their explicitly stated 2.7.0 assessment baseline.




Human-authorized legacy note cleanup: note_cleanup(project,actor) previews counts only. confirm=true backs up selected rows into .scrum/local/cleanup-backups/legacy-notes-*.json, flushes the backup, then transactionally deletes obsolete human Daily/Retrospective and automatic Agent pause handoff Reviews in that project. Other human Reviews, explicit AI reminders and task state/evidence/history remain intact. Repeated empty cleanup writes nothing. This is optional maintenance, never an agent startup step or a provider-token optimization by itself: excluded history was already absent from routine context.

Task models: the task editor accepts a fixed model override (blank means automatic), automatic tier and reason, and shows the latest launch decision. Priority is human task override, human profile model, then automatic. Agents can choose a tier/reason in ordinary task planning, but cannot replace fixed human choices. Codex automatic routes use known IDs found in the local model cache; unknown catalogs keep the provider default honestly. Claude uses documented haiku/sonnet/opus aliases without account probing. No inference is called for routing. Model selection and reason are recorded before launch. Manual changes during a live task apply at the next launch, without killing the current process. A task model does not change a desktop conversation’s model.

Compact AI context: task_get defaults to the agent view; full=true preserves the human editor view. work_start(id=...) reads only the offered task. next_work keeps the same compact view and conditional version. Managed Codex exposes eleven routine Scrum tools and hides optional Scrum skill catalog entries only for that run, retaining existing explicit skill preferences and all non-Scrum skills. Specialists remain readable on demand and reachable via the bounded stdio client; sandbox/approval policy is unchanged. task_plan accepts automatic model_tier/model_reason in its existing batch.

Verification reuse: scripts/verify.py --files ... --run --reuse reuses only a prior actual successful check with the same conservative source-tree hash, runtime and check bundle. It reports original_verified_at and never claims a new run. Any changed/added/deleted input invalidates it; inaccessible, symbolic-linked or oversized source trees disable reuse. Sources changing during checks cannot create a cached pass. Ignored local board/data/log outputs are excluded. External integration state and browser validation still require fresh checks. Cache files are checkout-local ignored data, not provider-token measurements.

Project parallelism: max_parallel_agents defaults to one (human-configurable 1..20 in Settings/MCP). Preflight offers and actual agent claims share atomic project capacity, independent of per-agent WIP. Supervisors renew startup slots through existing heartbeat calls until process exit; there is no extra model polling. Lowering the limit does not interrupt live work; existing actors can finish/resume, new actors defer. Portable snapshots carry the policy; old snapshots default to one. Human claims remain exempt. No extra workers or provider runs are created to test this feature.

Managed context batches: see [core guidance](skills/scrum-work/references/core-guidance.md) for claim, completion, continuation and Stop rules.

Reported CLI usage: existing Codex turn.completed or Claude result usage fields are read from a bounded tail of the current execution log. The supervisor attaches one report to its already-required checkpoint; local run UUIDs prevent double counting during cleanup/retry. coordination_metrics(include_cli_usage=true) returns actual reported counters only on demand; ordinary task/startup reads omit them. Missing fields remain null and coverage is explicit (terminal completed Codex turn or Claude run summary). Claude cache reads/writes are normalized with its base input only when all necessary fields exist; Codex cache/reasoning are subsets, not additional usage to sum. Batch counts are shared project execution data, never repeated/divided into per-task costs. This uses no extra model request and makes no monetary/quota saving estimate. Execution telemetry stays checkout-local, outside portable task snapshots.

Managed Codex MCP discovery uses --worker-schema: supervisor-only checkpoint fields (including the CLI usage payload) are omitted from the worker-visible schema, so telemetry does not add those definitions to the model context. Normal human/supervisor MCP schemas and validation remain unchanged.


Idle efficiency: supervisors watch a local control-change marker at 2-second intervals (no MCP request), checkpoint every 60 seconds idle / 10 seconds running, and react immediately on control or process transitions. Missing marker delivery falls back to the next checkpoint. Liveness expires after 90 seconds idle / 30 seconds running. Quota probes continue for active/pending work and human-enabled autoresume; idle cached readings keep their original observation time. Web polling uses browser-only conditional revisions; unchanged pages return only changed=false plus a version. Agents loads no task/note bodies. MCP counters still include actual supervisor calls and do not represent tokens.

Verification level (auto/minimal/targeted/full) can be set during normal planning; it records intent, not successful evidence or permission to skip mandatory checks. Record actual verification evidence.
# Board and task list

The Board toolbar switches between Kanban and Task list. Both share search and category filters. List columns sort in either direction; group by status, priority, owner, category or agent batch. View preferences stay in this browser per project. Task titles and codes open the existing editor.

Agent batch grouping uses real memberships. New multi-task batches keep a small checkout-local history after release; tasks show their latest recorded membership. Pre-existing completed runs with no saved membership remain ungrouped. Active groups show task reservations. History is browser-only, is excluded from normal MCP task reads and portable snapshots, and clears on project restore. Sorting/grouping performs no model calls or additional polling.

## License

This project is licensed under the [MIT License](LICENSE).
