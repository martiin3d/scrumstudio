# Verification and measurement

Run `python scripts/verify.py --files PATH --run --reuse` for the changed behavior. The helper maps files to targeted tests and returns compact evidence. A reused pass must match the checked inputs and keeps its original timestamp. Expand the scope when the change spans contracts or introduces unresolved risks.

Preview the plan by omitting `--run`. The `risk` entries list the changed paths, the reason and checks selected for schema, permissions and Git delivery. Use `--base REF` to include committed changes relative to a known base. Review `diff_unavailable` when Git could not inspect a path. The content rules are signals alongside the existing file-based test selection.

Keep review findings tied to the behavior, path and revision that exposed them. When a revision addresses a finding, verify its outcome and the affected diff. Human approval follows the task review state in [reviews.py](../../reviews.py).

Use `python scrum.py help task_review_findings` for the native findings operation. Record a stable issue key and relative path. The open findings page is enough for the next revision. Resolve a finding with the checked Git SHA and evidence.

Run the usage audit on demand:

```powershell
'{"include_audit":true}' | python scrum.py metrics --project PATH --stdin
```

`startup_samples` includes UTF-8 prompt bytes, section sizes for native CLI runs, recorded coordination calls and available provider counters. Compare equivalent work with the same client, model, acceptance and outcome. Check uncached input and calls alongside total input. A smaller initial prompt alone does not measure full-session savings.

`repetitions` reports recurring reads of already claimed tasks and executed checks with matching verification signatures. Use the counts to find sessions worth inspecting. The same task read can be warranted by pagination, updated scope, or recovery.

## Worker behavior evaluation

Run `python scripts/eval_worker.py --cases` for the new claim, resume and Git recovery scenarios. Use a disposable copy of the same starting fixture for each trial. Run the baseline and candidate with the same model, client and tool configuration, changing only the rules under evaluation. Use the normal Scrum supervisor and quota policy for model runs.

Save each variant as a JSON array. Each trial records `case`, `replicate` (a positive integer), `client`, `model`, `fixture_sha256`, `checks`, `calls` and optional `usage`. Record a boolean for each published check from observed files, verification receipts and task state. Provide the complete tool call list using normalized labels. Use the published redundant labels only when the trace shows the exact condition, for example `show_same_scope` for refetching a scope already supplied in the claim. Copy native provider counters into `usage` when available.

Compare with `python scripts/eval_worker.py --baseline BEFORE.json --candidate AFTER.json`. The report includes per-pair results and calculates cost deltas only for pairs that pass both variants. Missing counters remain unknown.

For an opt-in rule or skill comparison, use `--behavioral` with matched new-claim or resume fixtures. At launch, call `capture_layer(kind, name, workflow, controls, loaded)` with the exact rule/skill bytes handed to the worker, or `None` for the absent variant. Pin fixed inputs under `launch` and `verification` in `controls`, include other fixed skill/config bytes if relevant, and use the same model and verification on both sides. Put the returned manifest under `layer` in each observation with observed `tool_actions` and `seconds`. The report includes revisions and a recommendation for human review. It needs repeated, verified pairs with known cost to recommend a cost-driven change.

Prepare matching isolated boards by running `python scripts/eval_worker.py --fixture EMPTY_DIR --prepare CASE` once per variant. The command creates a local Git scenario and its first task, and prints the fixture-specific registry and legacy database paths. Set `SCRUM_REGISTRY` and `SCRUM_DB` to those paths when starting the corresponding isolated Scrum supervisor. Launch it through normal Wakeup and quota handling. Use the same client and model in both variants.

Read the run ID in the fixture's usage audit, then collect one trial with `python scripts/eval_worker.py --fixture DIR --collect RUN_ID`. This reads task state, fixture outputs, scoped coordination and provider counters. For the resume case, supply `--observed FILE.json` with `remaining_check_executed` confirmed from independent verification evidence. The file can also include `verified_redundant_calls` for semantic events that require inspecting the trace. Put the three collected trials for each variant into its comparison array.

## Installation diagnosis

Run `python scripts/setup.py --doctor --client both` to inspect bundled skill drift, duplicate hooks and effective MCP selection. The plan lists the files it would change and shows hashes. Apply that exact plan with `--apply-repair PLAN_ID --client both`. Restore its verified backup with `--restore-repair BACKUP_ID`. MCP selections are reported so they can be corrected through the project's normal settings.

## Wiki maintenance

Choose **Add project** in Scrum Studio to scan its folder and preview proposed pages. Answer the goal, first milestone and acceptance questions before applying the plan. **Wiki → Project setup** reopens a saved preview or an interrupted application. Existing topic files are kept and the index gains links to generated topics.

In **Wiki → Proposed updates**, open the source task to inspect its evidence. Edit the draft before applying it to the working folder, or dismiss it. Publish the reviewed files through the usual Git flow.

Use **Link a source** to associate an existing repository file with a topic and optional code paths. The source stays in place. **Check sources** compares observed hashes with the versioned `docs/agents/sources.json` and shows affected pages. Refresh updates generated pages that still match their recorded hashes. Review edited pages in place.

The setup questions reflect detected stack signals and manifest commands. In **Check sources**, accepting or deferring a capability records the decision in the versioned source manifest. Include that manifest and the Markdown pages when transferring a checkout. Export the Scrum snapshot to carry the first milestone and its specification link.

A `task_plan` item can include `"specification": {"page": "docs/agents/project.md", "section": "first-milestone"}`. The task records the section and its revision. A changed section is flagged in the claim along with relevant wiki links. When a continuation has a local checkpoint, the claim also identifies observed changes to the checkout, evidence or planned files. Local checkpoints live in the checkout. The wiki, source records and task specification references can travel with the project.

The usage audit distinguishes identical versioned task reads from older same-task counts. It records signatures of the requested page and returned version, without keeping request contents.

## Verify a handoff in another environment

1. Export the source project with `python scrum.py call project_export --project PATH`. Transfer the versioned wiki and source tree plus the returned `.scrum/project.json` and `.scrum/snapshot.json`.
2. Use a separate checkout and registry on the receiving machine or agent environment. Open it with the native `project_open` operation. Resolve the task by code and the worker by identity in the receiving registry. Inspect the Wiki, onboarding status, pinned specification and relevant decisions.
3. Check the task's verification status before running anything. Local verification receipts stay with their original checkout and the fresh checkout reports unknown. Use the native claim and the relevant verification helper to establish evidence in the receiving environment. Run again with `--reuse` to confirm the original timestamp survives. A covered input edit should change the receipt's status.

SCR-128 tested this flow on the same Windows host with an independent Python virtual environment, checkout and registry. The exported onboarding state, wiki link, pinned section and decision arrived intact. A passing JavaScript syntax receipt was unknown immediately after import. After executing the check on the receiver, identical inputs reused its timestamp and an edit to `public/app.js` marked it changed. This trial covered an isolated agent environment.

Run `python scripts/wiki_doctor.py` to find broken local links, missing Markdown anchors, topic pages absent from the index, and unresolved local Git references. Use the `git:REVISION` destination in a Markdown link when a local commit reference should be checked. The verifier also runs the doctor for changes under this wiki or to `AGENTS.md`.

When finishing a task with a durable decision or a reusable procedure proven by its evidence, use the existing `decisions` field. The optional `wiki_page` names an existing topic such as `docs/agents/git.md`. Scrum can infer a single topic from recognized source files when the page is omitted. Read the drafts with `python scrum.py call project_wiki_proposals --project PATH`. To find the full source task and evidence, use `python scrum.py show --project PATH --task CODE` with the proposal's `source_code`.

Review the proposed Markdown and copy the parts that belong in the page, preserving the draft's HTML marker. The marker records that the suggestion was addressed and keeps it out of the pending list. Publish the Markdown edit through the normal Git flow.
