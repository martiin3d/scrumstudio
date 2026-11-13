# Verification and measurement

Run `python scripts/verify.py --files PATH --run --reuse` for the changed behavior. The helper maps files to targeted tests and returns compact evidence. A reused pass must match the checked inputs and keeps its original timestamp. Expand the scope when the change spans contracts or introduces unresolved risks.

Keep review findings tied to the behavior, path and revision that exposed them. When a revision addresses a finding, verify its outcome and the affected diff. Human approval follows the task review state in [reviews.py](../../reviews.py).

Use `python scrum.py help task_review_findings` for the native findings operation. Record a stable issue key and relative path. The open findings page is enough for the next revision. Resolve a finding with the checked Git SHA and evidence.

Run the usage audit on demand:

```powershell
'{"include_audit":true}' | python scrum.py metrics --project PATH --stdin
```

`startup_samples` includes UTF-8 prompt bytes, section sizes for native CLI runs, recorded coordination calls and available provider counters. Compare equivalent work with the same client, model, acceptance and outcome. Check uncached input and calls alongside total input. A smaller initial prompt alone does not measure full-session savings.

## Installation diagnosis

Run `python scripts/setup.py --doctor --client both` to inspect bundled skill drift, duplicate hooks and effective MCP selection. The plan lists the files it would change and shows hashes. Apply that exact plan with `--apply-repair PLAN_ID --client both`. Restore its verified backup with `--restore-repair BACKUP_ID`. MCP selections are reported so they can be corrected through the project's normal settings.
