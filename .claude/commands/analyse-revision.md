Analyse a Cloud Run revision's run logs and scores to find improvements for the Tripletex AI agent.

## Input

The user provides a revision ID (e.g. `tripletex-agent-00015-dtp`). If not provided, ask for it.

## Step 1: Gather data

1. Check for local logs first at `example_runs/tripletex-agent/{revision}/`. If not found, download from GCS:
   ```
   gsutil -m cp -r "gs://tripletex-ai-agent-logs/runs/tripletex-agent/{revision}/" "example_runs/tripletex-agent/{revision}/"
   ```
2. Read `initial_scores.json` and `latest_scores.json` from the revision folder.
3. List all `*_run.txt` files per task subfolder.

## Step 2: Score analysis

1. Compute the attempt delta per task: `latest.total_attempts - initial.total_attempts` = runs on this revision.
2. Verify the file count per task matches the attempt delta (sanity check for logging issues).
3. Build a score table with columns: Task, Best Score, Max Score (tier 1=2.0, tier 2=4.0, tier 3=6.0), Tier, % of Max, Potential Gain.
4. Sort by potential gain descending — these are the highest-impact improvement targets.

Tier assignments:
- Tier 1 (×1, max 2.0): task_01-08 (foundational: create employee, customer, invoice, departments, etc.)
- Tier 2 (×2, max 4.0): task_09-18 (multi-step: invoice with payment, supplier invoices, credit notes, project billing)
- Tier 3 (×3, max 6.0): task_19-30 (complex: bank reconciliation, error correction, year-end closing)

## Step 3: Read run logs

For tasks with the worst scores relative to their tier max, read the `*_run.txt` files. Focus on:
- **422 errors**: What field names was the agent trying? What did the API say was wrong?
- **403 errors**: Is the agent hitting a [BETA] endpoint?
- **404 errors**: Wrong endpoint path?
- **Tool call limit reached**: Agent spinning on wrong field names or endpoints?
- **Unnecessary API calls**: Extra GETs, searches, or verification calls that could be skipped?
- **Wrong strategy**: Creating entities that should have been searched for, or vice versa?
- **Planner skip**: Did `[PHASE] Skipping planner` appear? Was the playbook classification correct?
- **Effort level**: Check `[PHASE] Executor effort=X` — was the effort appropriate for the task?
- **Timing**: Was Phase 1 too slow (>60s)? Was the total >300s? Where did time go (model thinking vs API)?
- **File attachments**: For PDF tasks, was `[FILE]` logged? Did the agent extract data correctly?

## Step 4: Cross-reference with API spec

For each error pattern found:
1. Search the OpenAPI spec (`docs/task_api_docs/apispec_openapi.json`) for the correct endpoint and field names.
2. Check if the endpoint is marked `[BETA]` in its summary (always returns 403).
3. Identify the correct non-beta alternative if applicable.
4. Verify correct field names in the request body schema.

## Step 5: Propose and implement fixes

Present findings as a table:

| Task | Issue | Root Cause | Fix | Expected Gain |
|------|-------|------------|-----|---------------|

Then implement fixes in priority order (highest potential gain first). Typical fix locations:
- `src/prompts/system_prompt.py` — Add/update Lessons Learned for field name issues, endpoint redirects, strategy corrections.
- `src/services/openapi_spec.py` — Filter out problematic endpoints from search results.
- `src/services/agent_service.py` — Adjust tool call limits, model settings, or tool definitions.

## Step 6: Summary

After implementing, present:
1. All changes made with file links.
2. Expected score improvements per task.
3. Suggest which tasks to re-run first (highest potential gain).

## Step 7: Verify locally before deploying

After implementing fixes, run `/test-local` to smoke-test with the local simulator:

```bash
python test_local.py task_1 task_2 task_4    # quick tier 1 check
python test_local.py                          # full run
```

This verifies:
- Agent starts up and responds correctly
- No regressions on existing tasks
- Pre-validator catches are logged (look for `[VALIDATION]` in output)
- No new API errors introduced

Only recommend deploying if the local test passes clean.

## Important notes

- The leaderboard tracks BEST score per task across ALL attempts — a bad run never lowers the score.
- Scoring: correctness (0-1) * tier. Efficiency bonus only when correctness = 1.0: `tier + tier * (optimal_calls/actual_calls * max(0, 1 - errors*0.15))`.
- Max per task: tier 1 = 2.0, tier 2 = 4.0, tier 3 = 6.0.
- Each 4xx error reduces efficiency bonus by 15%.
- Focus on correctness first (much bigger impact than efficiency).
- 5 attempts per task per day limit — prioritise high-value improvements.
- The local simulator covers 6 of 30 tasks — it's a smoke test, not ground truth.
