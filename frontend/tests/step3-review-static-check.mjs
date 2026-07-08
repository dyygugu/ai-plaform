import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import ts from "typescript";

const root = resolve(import.meta.dirname, "..");
const helperPath = resolve(root, "src/pages/abilityWorkbenchReview.ts");
const pagePath = resolve(root, "src/pages/AbilityWorkbenchPage.tsx");

assert.ok(existsSync(helperPath), "Step3 必须抽出可测试的通用审核 helper");

const helperSource = readFileSync(helperPath, "utf8");
const transpiled = ts.transpileModule(helperSource, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2022,
    verbatimModuleSyntax: false,
  },
});
const tmp = mkdtempSync(join(tmpdir(), "aidp-step3-review-"));
const compiledPath = join(tmp, "abilityWorkbenchReview.mjs");
writeFileSync(compiledPath, transpiled.outputText, "utf8");
const { buildStep3ReviewSummary } = await import(pathToFileURL(compiledPath).href);

try {
  const baseResult = {
    ok: true,
    task_id: "7658232870117527347",
    task_name: "Blender_3D 人标支持-0703",
    draft_id: "draft-3d",
    report_id: "report-3d",
    stage: "live_http_test",
    writes_remote: false,
    submits_remote: false,
    sends_network: true,
    queue_snapshot: {},
    question_context: { item_id: "7658288177744908083" },
    ai_decision: {
      rubric_items: [
        { rubric_id: "S1-1", verdict: "满足", reason: "候选图能看出主体模型与参考一致，关键结构没有明显缺失。" },
        { rubric_id: "S2-4", verdict: "不满足", reason: "候选图背景和构图氛围与参考有明显差异，不能视为完全一致。" },
      ],
    },
    answer_preview: {},
    saved_answer: {},
    saved_to_task_ui: false,
    temp_draft_result: {},
    temp_draft_payload_preview: {},
    ui_review_hint: "3D 预览阶段仅供人工审核。",
    review_status: "preview_only",
    review_artifact_path: "data/task-abilities/report.json",
    message: "ok",
  };

  const summary = buildStep3ReviewSummary(baseResult, "7630778503730253620");
  assert.equal(summary.status, "needs_review", "未写入任务页时不能直接放行 Step4");
  assert.equal(summary.rows.length, 2, "Step3 应按 qwen 输出顺序展示原因行");
  assert.equal(summary.rows[1].order, 2, "原因行顺序必须稳定");
  assert.match(summary.rows[1].qwenReason, /构图氛围/, "原因必须来自 qwen 输出，而不是前端臆造");

  const duplicateReason = buildStep3ReviewSummary({
    ...baseResult,
    ai_decision: {
      rubric_items: [
        { rubric_id: "S1-1", verdict: "不满足", reason: "候选图与参考图存在明显结构差异。" },
        { rubric_id: "S1-2", verdict: "不满足", reason: "候选图与参考图存在明显结构差异。" },
      ],
    },
  }, "7630778503730253620");
  assert.equal(duplicateReason.rows.length, 2, "不同 rubric 即使原因相同，也必须按题目顺序保留两行");

  const submitted = buildStep3ReviewSummary({ ...baseResult, submits_remote: true }, "7630778503730253620");
  assert.equal(submitted.status, "blocked", "Step3 发现正式提交必须阻断");

  const tooPrecise = buildStep3ReviewSummary({
    ...baseResult,
    saved_to_task_ui: true,
    ai_decision: {
      rubric_items: [
        { rubric_id: "S1-1", verdict: "满足", reason: "候选图颜色为 #33a7ff，边缘偏移 12px。" },
      ],
    },
  }, "7630778503730253620");
  assert.equal(tooPrecise.rows[0].assistantJudgement, "需人工复核", "非人类精确值应被标记复核");
  assert.equal(tooPrecise.status, "needs_review", "存在非人类精确值不能自动通过");

  const missingReason = buildStep3ReviewSummary({
    ...baseResult,
    saved_to_task_ui: true,
    ai_decision: { rubric_items: [{ rubric_id: "S1-1", verdict: "不满足", reason: "" }] },
  }, "7630778503730253620");
  assert.equal(missingReason.status, "blocked", "缺少 qwen 原因时不能通过");

  const onlyPlatformHint = buildStep3ReviewSummary({
    ...baseResult,
    saved_to_task_ui: true,
    ai_decision: {},
    answer_preview: {},
    saved_answer: {},
    ui_review_hint: "平台提示不是 qwen 原因。",
    message: "接口执行成功。",
  }, "7630778503730253620");
  assert.equal(onlyPlatformHint.status, "blocked", "不能把平台提示当成 qwen 原因放行");
  assert.equal(onlyPlatformHint.rows.length, 0, "没有 qwen 原因时不应伪造原因行");

  const previewOnlyReason = buildStep3ReviewSummary({
    ...baseResult,
    saved_to_task_ui: true,
    ai_decision: {},
    answer_preview: {
      "rubric_items.S1-B1.reason": "这是答案预览字段，不是 qwen 原始输出原因。",
    },
  }, "7630778503730253620");
  assert.equal(previewOnlyReason.status, "blocked", "不能把答案预览字段当成 qwen 原因放行");
  assert.equal(previewOnlyReason.rows.length, 0, "Step3 原因行只能来自 qwen ai_decision");

  const dimensionReasons = buildStep3ReviewSummary({
    ...baseResult,
    saved_to_task_ui: true,
    ai_decision: {
      rubric_items: [
        { rubric_id: "S1-1", verdict: "满足", reason: "主体形态与参考一致。" },
      ],
      dimension_scores: {
        S1: { score: 5, reason: "基础形体和主要部件整体匹配。" },
        S2: { score: 3, reason: "材质与构图有局部差异。" },
      },
      discard: { verdict: "No", reason: "没有出现空图、错图或无法判断的问题。" },
      rubrics_reasonable_reason: "rubric 判定覆盖了主体、细节和整体效果。",
    },
  }, "7630778503730253620");
  assert.equal(dimensionReasons.status, "allow", "完整 qwen 原因且已暂存时应允许进入 Step4");
  assert.equal(dimensionReasons.rows.length, 5, "同一个 qwen 对象内的 reason 不应被重复展示");
  assert.ok(dimensionReasons.rows.some((row) => row.qwenReason.includes("基础形体")), "Step3 应抽取 dimension_scores 原因");
  assert.ok(dimensionReasons.rows.some((row) => row.qwenReason.includes("无法判断")), "Step3 应抽取 discard 原因");
  assert.ok(dimensionReasons.rows.some((row) => row.qwenReason.includes("覆盖了主体")), "Step3 应抽取 reason 后缀字段");

  const page = readFileSync(pagePath, "utf8");
  assert.match(page, /buildStep3ReviewSummary/, "Step3 页面必须使用通用审核 helper");
  assert.match(page, /openAccountTarget/, "直达账号任务必须复用现有 POST 打开接口");
  assert.match(page, /handleOpenLiveAccountTask/, "Step3 需要通过按钮动作打开账号任务页");
  assert.match(page, /use_system_ai_for_vision:\s*false/, "Step3 live 测试默认必须调用做题 AI/qwen，而不是系统 AI");
  assert.match(page, /审核结果台/, "Step3 标题应改为通用审核结果台");
  assert.match(page, /直达账号任务/, "Step3 需要提供直达账号任务按钮");
  assert.match(page, /canOperateStep4/, "Step3 下一步必须使用 helper 结论控制");
  assert.match(page, /canOperateStep4/, "Step4 操作按钮必须继续受 Step3 helper 结论约束");
  assert.doesNotMatch(page, /key:\s*"request"[\s\S]*?Payload/, "Payload 调试不能继续作为 Step3 主视图");
} finally {
  rmSync(tmp, { recursive: true, force: true });
}
