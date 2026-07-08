import type { TaskLiveHttpTestResponse } from "../api/client";

export type Step3ReviewStatus = "allow" | "needs_review" | "blocked";
export type Step3AssistantJudgement = "通过" | "需人工复核" | "不允许通过";
export type Step3AlertType = "success" | "info" | "warning" | "error";

export interface Step3ReviewRow {
  key: string;
  order: number;
  itemId: string;
  qwenOutput: string;
  qwenReason: string;
  assistantJudgement: Step3AssistantJudgement;
  judgementReason: string;
}

export interface Step3ReviewSummary {
  status: Step3ReviewStatus;
  statusLabel: string;
  alertType: Step3AlertType;
  conclusion: string;
  canEnterStep4: boolean;
  itemId: string;
  reportId: string;
  reviewStatus: string;
  boundaryLabel: string;
  savedLabel: string;
  submittedLabel: string;
  rows: Step3ReviewRow[];
}

type JsonRecord = Record<string, unknown>;

interface RawReasonEntry {
  source: string;
  verdict: string;
  reason: string;
}

const HUMAN_UNLIKELY_PRECISION_PATTERN =
  /#[0-9a-f]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\(|\b\d+(?:\.\d+)?\s*(?:px|像素|%|mm|cm|deg|°)\b|坐标|色号|十六进制色值/i;

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function compactText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function firstText(record: JsonRecord, keys: string[]): string {
  for (const key of keys) {
    const text = asText(record[key]);
    if (text) return text;
  }
  return "";
}

function pickReason(record: JsonRecord): string {
  return firstText(record, ["reason", "rationale", "explanation", "analysis", "why", "依据", "原因", "说明"]);
}

function pickVerdict(record: JsonRecord): string {
  const direct = firstText(record, ["verdict", "status", "result", "judgement", "judgment", "decision", "answer", "score", "value"]);
  if (direct) return direct;
  if (typeof record.satisfied === "boolean") return record.satisfied ? "满足" : "不满足";
  if (typeof record.pass === "boolean") return record.pass ? "通过" : "不通过";
  return "-";
}

function pickSource(record: JsonRecord, fallback: string): string {
  return firstText(record, ["rubric_id", "id", "key", "name", "title", "dimension", "field", "label", "question_id"]) || fallback;
}

function getArray(record: JsonRecord, keys: string[]): unknown[] {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) return value;
  }
  return [];
}

function collectRubricReasons(result: TaskLiveHttpTestResponse): RawReasonEntry[] {
  const aiDecision = isRecord(result.ai_decision) ? result.ai_decision : {};
  const candidates = [
    ...getArray(aiDecision, ["rubric_items", "rubrics", "rubric_results", "criteria", "criteria_results"]),
  ];
  return candidates
    .filter(isRecord)
    .map((item, index) => ({
      source: pickSource(item, `第 ${index + 1} 项`),
      verdict: pickVerdict(item),
      reason: pickReason(item),
    }));
}

function collectNestedReasonFields(value: unknown, path = "AI 输出", entries: RawReasonEntry[] = []): RawReasonEntry[] {
  if (entries.length >= 80) return entries;
  if (Array.isArray(value)) {
    value.forEach((item, index) => collectNestedReasonFields(item, `${path} ${index + 1}`, entries));
    return entries;
  }
  if (!isRecord(value)) return entries;

  const reason = pickReason(value);
  if (reason) {
    entries.push({
      source: pickSource(value, path),
      verdict: pickVerdict(value),
      reason,
    });
  }

  for (const [key, child] of Object.entries(value)) {
    if (/(reason|rationale|explanation|analysis|why|依据|原因|说明)$/i.test(key)) {
      const reasonText = asText(child);
      if (reasonText && reasonText === reason) {
        continue;
      }
      if (reasonText) {
        entries.push({
          source: `${path}.${key}`,
          verdict: pickVerdict(value),
          reason: reasonText,
        });
      }
      continue;
    }
    if (isRecord(child) || Array.isArray(child)) {
      collectNestedReasonFields(child, `${path}.${key}`, entries);
    }
  }
  return entries;
}

function collectAdditionalReasons(result: TaskLiveHttpTestResponse): RawReasonEntry[] {
  const aiDecision = isRecord(result.ai_decision) ? { ...result.ai_decision } : {};
  for (const key of ["rubric_items", "rubrics", "rubric_results", "criteria", "criteria_results"]) {
    delete aiDecision[key];
  }
  return [
    ...collectNestedReasonFields(aiDecision, "qwen"),
  ];
}

function hasHumanUnlikelyPrecision(reason: string): boolean {
  return HUMAN_UNLIKELY_PRECISION_PATTERN.test(reason);
}

function judgeReason(reason: string, result: TaskLiveHttpTestResponse): Pick<Step3ReviewRow, "assistantJudgement" | "judgementReason"> {
  if (result.submits_remote) {
    return { assistantJudgement: "不允许通过", judgementReason: "Step3 不允许正式提交；检测到提交动作，必须阻断。" };
  }
  if (!reason.trim()) {
    return { assistantJudgement: "不允许通过", judgementReason: "qwen 没有给出可审核原因，无法按手册追溯。" };
  }
  if (hasHumanUnlikelyPrecision(reason)) {
    return { assistantJudgement: "需人工复核", judgementReason: "原因含色号、像素、坐标或百分比等非人类精确表达，需要人工确认。" };
  }
  return { assistantJudgement: "通过", judgementReason: "原因可读、可追溯，未发现提交越界或非人类精确表达。" };
}

function buildRows(result: TaskLiveHttpTestResponse): Step3ReviewRow[] {
  const itemId = compactText(result.question_context?.item_id) || compactText(result.ai_decision?.item_id) || "-";
  const seen = new Set<string>();
  const rawEntries = [...collectRubricReasons(result), ...collectAdditionalReasons(result)].filter((entry) => {
    const reason = entry.reason.trim();
    if (!reason) return true;
    const key = `${entry.source}::${entry.verdict}::${reason}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return rawEntries.map((entry, index) => ({
    key: `${index + 1}-${entry.source}`,
    order: index + 1,
    itemId,
    qwenOutput: `${entry.source}${entry.verdict && entry.verdict !== "-" ? `：${entry.verdict}` : ""}`,
    qwenReason: entry.reason,
    ...judgeReason(entry.reason, result),
  }));
}

function isErrorReviewStatus(reviewStatus: string): boolean {
  return /fail|error|reject|blocked|异常|失败|驳回/i.test(reviewStatus);
}

export function buildStep3ReviewSummary(result: TaskLiveHttpTestResponse): Step3ReviewSummary {
  const rows = buildRows(result);
  const hasBlockedRow = rows.some((row) => row.assistantJudgement === "不允许通过");
  const hasReviewRow = rows.some((row) => row.assistantJudgement === "需人工复核");
  const reviewStatus = result.review_status || "-";
  const itemId = compactText(result.question_context?.item_id) || compactText(result.ai_decision?.item_id) || "-";

  let status: Step3ReviewStatus = "allow";
  let conclusion = "允许进入 Step4：qwen 原因完整，边界未越界。仍需人工点开任务页核对已暂存内容后授权。";

  if (!result.ok || result.submits_remote || hasBlockedRow || rows.length === 0 || isErrorReviewStatus(reviewStatus)) {
    status = "blocked";
    conclusion = result.submits_remote
      ? "不允许通过：Step3 检测到正式提交动作，违反不提交边界。"
      : "不允许通过：qwen 输出或平台校验存在阻断项，需要先修正后重跑 Step1-3。";
  } else if (hasReviewRow || !result.saved_to_task_ui) {
    status = "needs_review";
    conclusion = !result.saved_to_task_ui
      ? "需人工复核：本次未确认写入任务页暂存，不能直接放行 Step4；请直达账号任务页核对。"
      : "需人工复核：qwen 原因中存在需要人工判断的表达，确认后再决定是否授权 Step4。";
  }

  const alertType: Step3AlertType = status === "allow" ? "success" : status === "blocked" ? "error" : "warning";
  const statusLabel = status === "allow" ? "允许通过" : status === "blocked" ? "不允许通过" : "需人工复核";

  return {
    status,
    statusLabel,
    alertType,
    conclusion,
    canEnterStep4: status === "allow",
    itemId,
    reportId: result.report_id || "-",
    reviewStatus,
    boundaryLabel: `${result.writes_remote ? "已写入暂存接口" : "未写入暂存接口"} / 正式提交：${result.submits_remote ? "是" : "否"}`,
    savedLabel: result.saved_to_task_ui ? "是" : "否",
    submittedLabel: result.submits_remote ? "是" : "否",
    rows,
  };
}
