# AIDP Answer Safety Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make future AIDP AI answering capabilities safe by default: no guessed HTTP payload writes, no false temp-save success, faster testable qwen message construction, and non-blocking account result collection.

**Architecture:** Keep the existing Step1-Step4 workbench and 3D fail-closed path. Fix only shared safety gates in the generic writer/prompt/runner layer; leave deprecated Bon8 and research-chart business-specific failures as historical unless they share the generic safety path.

**Tech Stack:** FastAPI/Python backend, pytest, React/Vite frontend static checks, existing file-based task ability state.

---

## Target

- Prevent any future non-3D task ability from writing a real `SubmitTempItemAnswer` payload unless a recorded or otherwise verified temp-save payload exists.
- Require explicit server acceptance (`BaseResp.StatusCode == 0`) before treating temp save as successful.
- Stop qwen message tests and future调教 from blocking on long external image downloads.
- Preserve multi-account fan-out while avoiding ordered `future.result()` result collection stalls.

## Scope

- Modify `backend/app/services/task_ability_service.py`.
- Modify `backend/app/services/task_auto_run_service.py`.
- Add/update focused tests in `backend/tests/test_task_ability_flow.py`, `backend/tests/test_task_ability_workbench_service.py`, and `backend/tests/test_task_auto_run_service.py`.
- Run backend focused tests, compile check, frontend Step1/Step3 static checks, and build.
- Call independent AI review after local verification.

## Not Doing

- Do not repair deprecated Bon8 prompt/runtime failures.
- Do not repair deprecated research-chart production submission endpoint to `SubmitItemAndReceive`.
- Do not deploy, restart services, change database, or write remote AIDP state in this phase.
- Do not infer any new task-specific payload shape from HTML or field names alone.

## Execution Steps

### Task 1: Fail Closed Without Verified Temp Payload

**Files:**
- Modify: `backend/tests/test_task_ability_flow.py`
- Modify: `backend/app/services/task_ability_service.py`

- [ ] Add a failing pytest proving `allow_temp_save=True` raises when `_load_recorded_temp_payload()` returns empty.
- [ ] Run the new test and confirm it fails because the code currently builds a guessed payload.
- [ ] Change `_build_temp_draft_payload()` so real temp-save requires a recorded payload; local/offline payload preview can remain read-only only if existing callers do not pass `allow_temp_save`.
- [ ] Run the new test and existing real-no-submit tests.

### Task 2: Strict Temp Save Success

**Files:**
- Modify: `backend/tests/test_task_ability_flow.py`
- Modify: `backend/app/services/task_ability_service.py`

- [ ] Add tests for `_temp_save_succeeded()` requiring `base_resp_status_code == 0`.
- [ ] Confirm `ok=True` with missing/`None` `BaseResp` fails the new test.
- [ ] Change `_temp_save_succeeded()` to return true only for `0` or `"0"`.
- [ ] Run focused flow tests.

### Task 3: Avoid qwen Message Image Download Stall

**Files:**
- Modify: `backend/tests/test_task_ability_workbench_service.py`
- Modify: `backend/app/services/task_ability_service.py`

- [ ] Add/adjust tests proving `_build_research_chart_ai_messages()` does not call `requests.get()` for ordinary URL inputs during message construction.
- [ ] Confirm the old behavior fails or times out under the test.
- [ ] Change `_prepare_research_chart_image_for_ai()` to return URLs unchanged by default, while keeping existing data URL passthrough.
- [ ] Run all `research_chart_task_ai_messages` tests and ensure they complete quickly.

### Task 4: Non-Blocking Multi-Account Result Collection

**Files:**
- Modify: `backend/tests/test_task_auto_run_service.py`
- Modify: `backend/app/services/task_auto_run_service.py`

- [ ] Add a test where a later account finishes before a slower earlier account and assert completed results are collected via `as_completed`.
- [ ] Confirm old ordered `future.result()` behavior fails timing/order assertions.
- [ ] Use `concurrent.futures.as_completed` for result collection while preserving existing evidence aggregation and per-account failure isolation.
- [ ] Run `test_task_auto_run_service.py`.

## Risks And Rollback

- Risk: Existing research-chart tests may expect fallback payload generation for local payload preview. Rollback: keep fallback only for read-only preview paths, but block when `allow_temp_save=True`.
- Risk: Some historical live reports may no longer approve because missing `BaseResp` was previously accepted. This is intended; rollback is not recommended for future safety.
- Risk: Returning image URLs unchanged may affect providers requiring data URLs. Rollback option: add an explicit opt-in prefetch flag for providers that require it; do not fetch during tests or generic prompt construction.
- Risk: `as_completed` can change internal processing order. The public account list order should remain stable because final state still iterates original `snapshot.accounts`.

## Verification

- `PYTHONPATH=backend py -3 -m pytest backend/tests/test_task_ability_flow.py -q`
- `PYTHONPATH=backend py -3 -m pytest backend/tests/test_task_ability_workbench_service.py -k "research_chart_task_ai_messages or 3d or run_gate or partial_missing_rubric_reason" -q`
- `PYTHONPATH=backend py -3 -m pytest backend/tests/test_task_auto_run_service.py -q`
- `py -3 -m compileall -q backend/app`
- `node frontend/tests/step1-sample-static-check.mjs`
- `node frontend/tests/step3-review-static-check.mjs`
- `npm --prefix frontend run build`
- `git diff --check`

## Acceptance Requirements

- No real temp-save path can proceed without a recorded/verified payload.
- Temp save is successful only when server acceptance is explicit.
- 3D remains fail-closed until field mapping and writer are verified.
- Old Bon8/research-chart failures are documented as historical and not used to claim future readiness.
- Independent review finds no unresolved blocker or important issue for the shared safety baseline.

## Acceptance Target

- Future task abilities can safely proceed through Step1-Step3 as review-only or verified temp-save flows.
- Step4 remains blocked unless Step3 has qwen reasons, no submit action, explicit temp-save success, and human approval.
- Multi-account flow remains parallel and does not serialize all evidence collection behind the first slow account.
