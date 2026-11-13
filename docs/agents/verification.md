# Verification and measurement

Run `python scripts/verify.py --files PATH --run --reuse` for the changed behavior. The helper maps files to targeted tests and returns compact evidence. A reused pass must match the checked inputs and keeps its original timestamp. Expand the scope when the change spans contracts or introduces unresolved risks.

Keep review findings tied to the behavior, path and revision that exposed them. When a revision addresses a finding, verify its outcome and the affected diff. Human approval follows the task review state in [reviews.py](../../reviews.py).

Run the usage audit on demand:

```powershell
'{"include_audit":true}' | python scrum.py metrics --project PATH --stdin
```

`startup_samples` includes UTF-8 prompt bytes, section sizes for native CLI runs, recorded coordination calls and available provider counters. Compare equivalent work with the same client, model, acceptance and outcome. Check uncached input and calls alongside total input. A smaller initial prompt alone does not measure full-session savings.
