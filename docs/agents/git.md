# Git delivery

- [git_sync.py](../../git_sync.py) prepares task branches, commits declared files and publishes an immutable source. [git_completion.py](../../git_completion.py) binds verification to the candidate and reconciles delivery.
- [git_worktrees.py](../../git_worktrees.py) owns managed worktrees and cleanup. Ask `project_workspaces` for a read-only preview of retention reasons. A squash content match is informational for cleanup.
- [git_paused.py](../../git_paused.py) and [git_retained.py](../../git_retained.py) preserve drafts and local files. Use the recorded receipt and task branch when diagnosing failures.

## Recovery history

Open **Updates & comments** on the delivery or recovery task in web or desktop. The delivery history records the original failure and automatic retries. A recovery card links back to the delivery and records agent-assisted actions and native verification. History keeps Git operation names, safe error categories and verified refs. It omits raw command arguments and output. A native publisher identifies the execution path. Its sandbox status is **Unknown** when the execution interface provides no sandbox metadata.

### Historical case · SCR-224 and SCR-225

SCR-224 recorded that native completion was invoked under Auto review with workspace-write active. The native Git history reports that source publication failed when the connection to GitHub port 443 could not be established. That failure triggered SCR-225 for `scrum/scr-224-organize-project-settings-into-tabs` → `main` on `origin`.

SCR-225's completion evidence reports reusing SCR-224's Edge and desktop WebView2 checks on the unchanged source, passing `git diff --check`, confirming the direct parent `0bbf85da06df609fe9239fc3bf947681a09ca1a3` on local and remote `main`, publishing a normal fast-forward, and independently confirming `refs/heads/main` at `8e3025ccd414bd9b5eef89c90ca0c2fed5038eae` with `git ls-remote`. The local commit IDs match those abbreviated in the evidence.

The saved task histories do not establish the exact command arguments, complete attempt sequence, per-action sandbox status, or whether an approval enabled any SCR-225 action. Those details are **Unknown**. SCR-224's earlier Full access continuity entry says that no completion or publication was attempted during that launch.

For protected GitHub branches, choose **Protected pull request** under Git automation. Scrum publishes the verified task SHA and reports `awaiting_pr`. Open and merge the PR through the project's normal GitHub workflow. Then use an authenticated GitHub CLI and reconcile by PR number:

```powershell
'{"id":123,"number":42}' | python scrum.py call task_pull_request --project PATH --actor HUMAN_ID --stdin
```

Reconciliation checks the recorded source and destination, reported successful CI, the published landing commit and its verified content. It handles squash landings and reports material changes for focused re-verification. Use the current task ID and human actor ID. Leave the task in review while a required check or approval is pending.
