# bon8 Run Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first executable bon8 production slice: file-backed run state, first-item review gate APIs, and old confirmation wording cleanup.

**Architecture:** Extend the existing `bon8_production` service rather than creating a parallel subsystem. First slice stores run snapshots as JSON under `data/production-runs/bon8-runs`, exposes start/status/approve/reject/stop APIs, and keeps real AI solve/remote SubmitItem execution behind later tested adapters.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy test session, Python `unittest`, React/TypeScript API client follow-up.

---

### Task 1: File-Backed Run State

**Files:**
- Modify: `backend/app/schemas/bon8_production.py`
- Modify: `backend/app/services/bon8_production_service.py`
- Test: `backend/tests/test_bon8_production_service.py`

- [x] Add tests proving `start_bon8_production` returns `run_id`, `mode=first_item_review`, `status=waiting_first_confirm`, creates one seed account, and writes a state JSON file.
- [x] Verify the test fails before production changes.
- [x] Add Pydantic response fields and file-backed run helpers.
- [x] Verify the test passes.

### Task 2: First Gate Decisions

**Files:**
- Modify: `backend/app/services/bon8_production_service.py`
- Modify: `backend/app/api/v1/routes/bon8_production.py`
- Test: `backend/tests/test_bon8_production_service.py`

- [x] Add tests proving `approve_bon8_run_confirmation` marks the first item as allowed but keeps the run in `waiting_first_submit` until SubmitItem/readback adapter is connected, and `reject_bon8_run_confirmation` blocks the run.
- [x] Verify tests fail before implementation.
- [x] Implement approve/reject/stop/read run helpers and routes.
- [x] Verify tests pass.

### Task 3: Old Wording Cleanup

**Files:**
- Modify: `backend/app/services/bon8_production_service.py`
- Modify: `backend/tests/test_bon8_production_service.py`

- [x] Add tests proving status messages and guardrails no longer mention early three-confirmation gating.
- [x] Verify tests fail before implementation.
- [x] Update messages to first-item review wording.
- [x] Verify tests pass.

### Task 4: Frontend Gate Console

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/AiPage.tsx`
- Test: `frontend/tests/operation-ux-static-check.mjs`

- [x] Add static assertions forbidding old three-confirmation, per-account count, and frontend auto-submit switch wording.
- [x] Verify the static check fails on the old UI.
- [x] Add run read/approve/reject/stop API client methods and render run/account/confirmation-sheet state in the AI page.
- [x] Keep approve UI honest: approved means waiting for first SubmitItem/readback, not full `auto_parallel`, until the real submit adapter lands.
- [x] Verify operation UX static check, frontend build, and backend bon8 service tests pass.
