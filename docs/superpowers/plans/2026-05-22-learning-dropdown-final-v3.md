# Learning Dropdown Final V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Step2 系统 AI 对话与学习包/回放上下文链路，补齐字段兼容与摘要能力，并完成最小可验证交付。

**Architecture:** 以 `backend/app/services/task_ability_service.py` 为中心收敛 Step2 chat 和 replay 上下文，在 `learning_package_service.py` 负责学习包选择与摘要，在 `frontend/src/pages/AbilityWorkbenchPage.tsx` 保持现有布局只修请求与错误表现。测试优先覆盖路由兼容、服务回退和上下文结构，再做最小实现。

**Tech Stack:** FastAPI, Pydantic, Python pytest, React, TypeScript, PowerShell 7

---

### Task 1: 补齐学习包字段兼容

**Files:**
- Modify: `backend/app/schemas/learning_package.py`
- Modify: `backend/app/api/v1/routes/task_abilities.py`
- Test: `backend/tests/test_task_ability_run_routes.py`

- [ ] **Step 1: 写失败测试，要求 selected-learning-package 接口接受 `selected_learning_package_id`**

```python
selected = client.post(
    "/api/v1/task-abilities/7639402643386830630/selected-learning-package",
    json={"selected_learning_package_id": "rec-1"},
)
assert selected.status_code == 200, selected.text
```

- [ ] **Step 2: 运行单测确认当前失败**

Run: `python -m pytest backend/tests/test_task_ability_run_routes.py -k "step2_routes or selected_learning_package_id" -v`
Expected: FAIL，提示 `learning_package_id 不能为空` 或请求体字段未被消费。

- [ ] **Step 3: 最小实现字段兼容**

```python
class SelectLearningPackageRequest(BaseModel):
    selected_learning_package_id: str = ""
    learning_package_id: str = ""
    recording_id: str = ""
```

```python
package_id = str(
    payload.selected_learning_package_id
    or payload.learning_package_id
    or payload.recording_id
    or ""
).strip()
```

- [ ] **Step 4: 重跑单测确认通过**

Run: `python -m pytest backend/tests/test_task_ability_run_routes.py -k "step2_routes or selected_learning_package_id" -v`
Expected: PASS

### Task 2: 补强学习包摘要与做题 AI 输入结构

**Files:**
- Modify: `backend/app/services/learning_package_service.py`
- Modify: `backend/app/services/task_ability_service.py`
- Test: `backend/tests/test_task_ability_workbench_service.py`

- [ ] **Step 1: 写失败测试，覆盖摘要里的解析失败原因和 current_item_input 扩展字段**

```python
assert "解析失败原因" in summary.summary_text
assert "web_url" in user_content
assert "extra_context" in user_content
```

- [ ] **Step 2: 运行单测确认失败**

Run: `python -m pytest backend/tests/test_task_ability_workbench_service.py -k "current_item_input or parse_failure" -v`
Expected: FAIL，缺少目标字段。

- [ ] **Step 3: 最小实现摘要补强和输入对象扩展**

```python
"current_item_input": {
    ...,
    "web_url": str(context.get("web_url") or ""),
    "page_fields": {...},
    "extra_context": context.get("extra_context") if isinstance(context.get("extra_context"), dict) else {},
}
```

```python
if parse_failure_reason:
    lines.append("解析失败原因：" + parse_failure_reason)
```

- [ ] **Step 4: 重跑单测确认通过**

Run: `python -m pytest backend/tests/test_task_ability_workbench_service.py -k "current_item_input or parse_failure" -v`
Expected: PASS

### Task 3: 复核 chat / replay 服务回退与上下文链路

**Files:**
- Modify: `backend/app/services/task_ability_service.py`
- Test: `backend/tests/test_task_ability_workbench_service.py`

- [ ] **Step 1: 写失败测试，覆盖无学习包、provider 400 回退、latest replay stale 和 context_summary 结构**

```python
assert result["provider_status"] == "provider_error_fallback"
assert result["context_summary"]["selected_learning_package_id"] == "rec-1"
assert result["context_summary"]["latest_replay_summary"]["is_stale_for_current_prompt"] is True
```

- [ ] **Step 2: 运行服务测试确认失败点**

Run: `python -m pytest backend/tests/test_task_ability_workbench_service.py -k "chat_task_ability or replay_report" -v`
Expected: 如有缺口则 FAIL；否则记录为已满足并不改行为。

- [ ] **Step 3: 仅在测试失败时做最小修复**

```python
answer = _local_task_ability_chat_answer(...) + f"\n\n系统 AI 服务暂不可用：{_sanitize_provider_error(str(exc))}"
```

- [ ] **Step 4: 重跑相关测试**

Run: `python -m pytest backend/tests/test_task_ability_workbench_service.py -k "chat_task_ability or replay_report" -v`
Expected: PASS

### Task 4: 前端只修字段和错误表现，不动 Step2 布局

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/AbilityWorkbenchPage.tsx`

- [ ] **Step 1: 对齐前端保存学习包接口字段类型**

```ts
export async function saveSelectedLearningPackage(
  taskId: string,
  payload: { selected_learning_package_id?: string; learning_package_id?: string; recording_id?: string }
): Promise<SelectLearningPackageResponse> { ... }
```

- [ ] **Step 2: 在页面保存选择时优先传 `selected_learning_package_id`**

```ts
const result = await saveSelectedLearningPackage(selectedTaskId, {
  selected_learning_package_id: learningPackageId,
});
```

- [ ] **Step 3: 保持 Step2 现有布局，仅保留错误提示**

```ts
message.error(safeError(error));
```

### Task 5: 最小验证

**Files:**
- Verify only

- [ ] **Step 1: 跑后端相关测试**

Run: `python -m pytest backend/tests/test_task_ability_run_routes.py backend/tests/test_task_ability_workbench_service.py backend/tests/test_operation_recordings.py -v`
Expected: PASS

- [ ] **Step 2: 跑前端 build**

Run: `npm run build`
Expected: exit 0

- [ ] **Step 3: 搜索关键字段确认链路**

Run: `rg -n "selected_learning_package_id|latest_replay_summary|current_item_input|task_id_candidates|platform_base_url" backend frontend`
Expected: 能看到前后端关键字段已对齐。
