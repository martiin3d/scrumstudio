# Git delivery

- [git_sync.py](../../git_sync.py) prepares task branches, commits declared files and publishes an immutable source. [git_completion.py](../../git_completion.py) binds verification to the candidate and reconciles delivery.
- [git_worktrees.py](../../git_worktrees.py) owns managed worktrees and cleanup. Ask `project_workspaces` for a read-only preview of retention reasons. A squash content match is informational for cleanup.
- [git_paused.py](../../git_paused.py) and [git_retained.py](../../git_retained.py) preserve drafts and local files. Use the recorded receipt and task branch when diagnosing failures.

For protected GitHub branches, choose **Protected pull request** under Git automation. Scrum publishes the verified task SHA and reports `awaiting_pr`. Open and merge the PR through the project's normal GitHub workflow. Then use an authenticated GitHub CLI and reconcile by PR number:

```powershell
'{"id":123,"number":42}' | python scrum.py call task_pull_request --project PATH --actor HUMAN_ID --stdin
```

Reconciliation checks the recorded source and destination, reported successful CI, the published landing commit and its verified content. It handles squash landings and reports material changes for focused re-verification. Use the current task ID and human actor ID. Leave the task in review while a required check or approval is pending.
