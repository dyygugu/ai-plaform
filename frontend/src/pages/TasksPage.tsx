import { Alert, Button, Card, Collapse, Descriptions, Drawer, Form, Image, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, Timeline, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";

import {
  fetchTaskCatalog,
  fetchTaskCapability,
  fetchTaskDetail,
  fetchTaskHttpQuestionContext,
  fetchTaskOperationProcessPlan,
  fetchProductionDashboard,
  openAccountTarget,
  fetchTaskAbilityDrafts,
  approveTaskCapabilityReview,
  buildTaskMediaInspectionDraft,
  buildTaskMediaInspectionPlan,
  buildTaskMediaInspectionProvider,
  buildTaskSandboxClickDraft,
  buildTaskSandboxClickPlan,
  extractTaskVideoKeyframes,
  executeTaskSandboxClicks,
  executeTaskMediaInspection,
  buildTaskCapabilityAiDraft,
  buildTaskCapabilityDraft,
  buildTaskCapabilityProviderDraft,
  checkTaskAutoRunPreflight,
  runTaskAbilityRealNoSubmit,
  fetchActiveTaskAutoRun,
  fetchTaskAutoRun,
  fetchTaskAutoProductionStatus,
  fetchTaskAutoRunWorkerStatus,
  fetchExecutionDevicesForProduction,
  refreshProductionAccounts,
  pauseAutoAnswerRun,
  resumeAutoAnswerRun,
  startTaskAutoRunWorker,
  stopAutoAnswerRun,
  stopTaskAutoRun,
  stopTaskAutoRunWorker,
  type TaskAutoRunAccountState,
  type TaskAutoRunAccountEvidence,
  type TaskAutoRunResponse,
  type TaskAutoRunPreflightCheck,
  type TaskAutoRunPreflightResponse,
  type TaskAutoRunWorkerStatusResponse,
  type AutoProductionStatusResponse,
  type ExecutionDeviceItem,
  type StartProductionPayload,
  type TaskAbilityRealNoSubmitResponse,
  type TaskCapabilityCardResponse,
  type TaskCapabilityFieldMapping,
  type TaskCapabilityInputSpec,
  type TaskCapabilityOutputField,
  type TaskCapabilityRule,
  type TaskHttpQuestionContextResponse,
  type TaskOperationProcessPlanResponse,
  type TaskQuestionDecisionStep,
  type TaskQuestionIterationCandidate,
  type TaskQuestionMaterialResource,
  type TaskSandboxClickCandidate,
  type TaskSandboxClickExecutionResponse,
  type TaskSandboxClickExecutionResult,
  type TaskSandboxClickPlanResponse,
  type TaskSandboxClickPlanStep,
  type TaskMediaInspectionPlanResponse,
  type TaskMediaInspectionExecutionResponse,
  type TaskMediaInspectionProviderResponse,
  type TaskMediaInspectionStep,
  type TaskMediaProbeResult,
  type TaskMediaResource,
  type TaskVideoKeyframeExtractionResponse,
  type TaskDraftConfirmationFieldDiff,
  type TaskDraftConfirmationGateStatus,
  type TaskDraftRehearsalChecklistItem,
  type TaskDraftReviewItem,
  type TaskDraftReviewApprovalResponse,
  type TaskDraftBuildResponse,
  type TaskCatalogDetailResponse,
  type TaskCatalogItem,
  type TaskCatalogResponse,
  type TaskAbilityDraftItem,
  type Bon8ProductionRunResponse,
  type ProductionAccountCard,
  type ProductionAccountRefreshResponse,
  type ProductionDashboardSummary,
  type ProductionTaskStat,
} from "../api/client";

const BON8_TASK_ID = "7637771731901861641";
const RESEARCH_CHART_TASK_IDS = new Set(["7638992213846740763", "7639402643386830630"]);

const statusColorMap: Record<TaskCatalogItem["task_status_color"], string> = {
  green: "green",
  blue: "blue",
  gray: "default",
  red: "red",
  yellow: "gold",
};

interface TaskQueueAccountRow {
  account: ProductionAccountCard;
  task: ProductionTaskStat;
}

interface TaskQueueRow {
  key: string;
  task_id: string;
  task_name: string;
  account_count: number;
  pending: number;
  processing: number;
  in_progress: number;
  repair: number;
  delivered: number;
  abandoned: number;
  stale_count: number;
  error_count: number;
  accounts: TaskQueueAccountRow[];
  catalog_item: TaskCatalogItem | null;
}

interface TaskAutoRunEvidenceRow extends TaskAutoRunAccountEvidence {
  account_user_id: string;
  account_name: string;
}

interface UnifiedNoSubmitResult {
  task_kind: "bon8" | "generic";
  stage: string;
  review_status: string;
  account_user_id: string;
  item_id: string;
  sends_network: boolean;
  writes_remote: boolean;
  submits_remote: boolean;
  saved_to_task_ui: boolean;
  answer_preview: Record<string, unknown>;
  temp_result: Record<string, unknown>;
  verify_result: Record<string, unknown>;
  review_artifact_path: string;
  message: string;
  ui_review_hint: string;
}

const defaultProductionForm = (): StartProductionPayload => ({
  account_scope: { mode: "all_available", account_user_ids: [] },
  question_scope: { mode: "pending" },
  execution_mode: "platform_plus_devices",
  device_scope: { mode: "auto", worker_ids: [] },
  limits: { max_items_total: null, failure_threshold: 3 },
});

function parsePendingCount(value: string) {
  const normalized = String(value || "").replace(/,/g, "");
  const match = normalized.match(/\d+/);
  return match ? Number(match[0]) : 0;
}

function taskCatalogDedupeKey(item: TaskCatalogItem) {
  return [item.source_account_user_id, item.task_id || item.task_name_id || item.raw_task_name].join("::");
}

function taskCatalogRank(item: TaskCatalogItem) {
  const seenAt = item.last_task_page_seen_at ? new Date(item.last_task_page_seen_at).getTime() : 0;
  return [seenAt, parsePendingCount(item.pending_raw), item.id] as const;
}

function dedupeTaskCatalogItems(items: TaskCatalogItem[]) {
  const selected = new Map<string, TaskCatalogItem>();
  for (const item of items) {
    const key = taskCatalogDedupeKey(item);
    const existing = selected.get(key);
    if (!existing) {
      selected.set(key, item);
      continue;
    }
    const currentRank = taskCatalogRank(item);
    const existingRank = taskCatalogRank(existing);
    if (
      currentRank[0] > existingRank[0] ||
      (currentRank[0] === existingRank[0] && currentRank[1] > existingRank[1]) ||
      (currentRank[0] === existingRank[0] && currentRank[1] === existingRank[1] && currentRank[2] > existingRank[2])
    ) {
      selected.set(key, item);
    }
  }
  return Array.from(selected.values()).sort((left, right) => {
    const pendingDelta = parsePendingCount(right.pending_raw) - parsePendingCount(left.pending_raw);
    if (pendingDelta !== 0) return pendingDelta;
    return (right.last_task_page_seen_at || "").localeCompare(left.last_task_page_seen_at || "");
  });
}

function taskQueueKey(task: ProductionTaskStat) {
  return task.task_id || task.task_name || task.source;
}

function taskCatalogByTaskId(items: TaskCatalogItem[]) {
  const result = new Map<string, TaskCatalogItem>();
  for (const item of dedupeTaskCatalogItems(items)) {
    if (item.task_id && !result.has(item.task_id)) result.set(item.task_id, item);
  }
  return result;
}

function buildTaskQueues(accounts: ProductionAccountCard[], catalogItems: TaskCatalogItem[]) {
  const catalogByTaskId = taskCatalogByTaskId(catalogItems);
  const queues = new Map<string, TaskQueueRow>();
  for (const account of accounts) {
    for (const task of account.task_stats) {
      const key = taskQueueKey(task);
      if (!key) continue;
      const existing = queues.get(key) ?? {
        key,
        task_id: task.task_id,
        task_name: task.task_name || task.task_id || "未命名任务",
        account_count: 0,
        pending: 0,
        processing: 0,
        in_progress: 0,
        repair: 0,
        delivered: 0,
        abandoned: 0,
        stale_count: 0,
        error_count: 0,
        accounts: [],
        catalog_item: task.task_id ? catalogByTaskId.get(task.task_id) ?? null : null,
      };
      existing.account_count += 1;
      existing.pending = Math.max(existing.pending, task.pending);
      existing.processing += task.processing;
      existing.in_progress += task.in_progress;
      existing.repair += task.repair;
      existing.delivered += task.delivered;
      existing.abandoned += task.abandoned;
      if (task.stale) existing.stale_count += 1;
      if (task.error) existing.error_count += 1;
      existing.accounts.push({ account, task });
      if (!existing.catalog_item && task.task_id) {
        existing.catalog_item = catalogByTaskId.get(task.task_id) ?? null;
      }
      queues.set(key, existing);
    }
  }
  return Array.from(queues.values()).sort((left, right) => {
    const accountDelta = right.account_count - left.account_count;
    if (accountDelta !== 0) return accountDelta;
    const pendingDelta = right.pending - left.pending;
    if (pendingDelta !== 0) return pendingDelta;
    const processingDelta = right.processing - left.processing;
    if (processingDelta !== 0) return processingDelta;
    const activeDelta = right.in_progress - left.in_progress;
    if (activeDelta !== 0) return activeDelta;
    return left.task_name.localeCompare(right.task_name);
  });
}

function renderPending(value: string, record: TaskCatalogItem) {
  if (record.last_task_page_error) {
    return <Typography.Text type="warning">未验证（旧缓存已隐藏）</Typography.Text>;
  }
  return value || "0";
}

function renderJsonValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function buildTaskAutoRunEvidenceRows(run: TaskAutoRunResponse | null): TaskAutoRunEvidenceRow[] {
  const account_evidence = run?.raw_adapter_run.account_evidence;
  if (!account_evidence) return [];
  return Object.entries(account_evidence).map(([account_user_id, evidence]) => {
    const account = run.accounts.find((item) => item.account_user_id === account_user_id);
    return {
      ...evidence,
      account_user_id,
      account_name: account?.account_name || account_user_id,
    };
  });
}

function renderChecklistStatus(value: string) {
  const colorMap: Record<string, string> = {
    ready: "green",
    blocked: "red",
    needs_operator: "gold",
    planned: "blue",
  };
  const labelMap: Record<string, string> = {
    ready: "就绪",
    blocked: "阻塞",
    needs_operator: "待人工",
    planned: "计划中",
  };
  return <Tag color={colorMap[value] ?? "default"}>{labelMap[value] ?? value}</Tag>;
}

function abilityFlowLabel(draft: TaskAbilityDraftItem | null | undefined, hasRecording: boolean) {
  if (draft?.capability_enabled) return { color: "green", text: "有做题能力" };
  if (draft?.flow_stage === "real_no_submit_review") return { color: "orange", text: "暂存待审核" };
  if (draft?.flow_stage === "real_no_submit_ready") return { color: "blue", text: "待暂存不提交" };
  if (draft) return { color: "gold", text: "草稿待审核" };
  if (hasRecording) return { color: "purple", text: "有录制待入库" };
  return { color: "gold", text: "待学习能力" };
}

const queueNumberColumn = (
  title: string,
  dataIndex: keyof Pick<TaskQueueRow, "account_count" | "pending" | "processing" | "in_progress" | "repair" | "delivered" | "abandoned">,
): ColumnsType<TaskQueueRow>[number] => ({
  title,
  dataIndex,
  key: dataIndex,
  width: 76,
  align: "right",
  className: "task-queue-number-column",
  sorter: (left, right) => Number(left[dataIndex] ?? 0) - Number(right[dataIndex] ?? 0),
});

const accountDetailNumberColumn = (
  title: string,
  dataIndex: keyof Pick<ProductionTaskStat, "processing" | "in_progress" | "repair" | "delivered" | "abandoned">,
): ColumnsType<TaskQueueAccountRow>[number] => ({
  title,
  dataIndex: ["task", dataIndex],
  key: dataIndex,
  width: 76,
  align: "right",
  className: "task-queue-number-column",
  sorter: (left, right) => Number(left.task[dataIndex] ?? 0) - Number(right.task[dataIndex] ?? 0),
});

export function TasksPage() {
  const [catalog, setCatalog] = useState<TaskCatalogResponse | null>(null);
  const [productionDashboard, setProductionDashboard] = useState<ProductionDashboardSummary | null>(null);
  const [taskAbilityDrafts, setTaskAbilityDrafts] = useState<TaskAbilityDraftItem[]>([]);
  const [selectedTaskQueueKey, setSelectedTaskQueueKey] = useState<string>("");
  const [expandedTaskQueueKeys, setExpandedTaskQueueKeys] = useState<string[]>([]);
  const [detail, setDetail] = useState<TaskCatalogDetailResponse | null>(null);
  const [capability, setCapability] = useState<TaskCapabilityCardResponse | null>(null);
  const [questionContext, setQuestionContext] = useState<TaskHttpQuestionContextResponse | null>(null);
  const [operationProcessPlan, setOperationProcessPlan] = useState<TaskOperationProcessPlanResponse | null>(null);
  const [sandboxClickPlan, setSandboxClickPlan] = useState<TaskSandboxClickPlanResponse | null>(null);
  const [sandboxClickExecution, setSandboxClickExecution] = useState<TaskSandboxClickExecutionResponse | null>(null);
  const [mediaInspectionPlan, setMediaInspectionPlan] = useState<TaskMediaInspectionPlanResponse | null>(null);
  const [mediaInspectionExecution, setMediaInspectionExecution] = useState<TaskMediaInspectionExecutionResponse | null>(null);
  const [mediaInspectionProvider, setMediaInspectionProvider] = useState<TaskMediaInspectionProviderResponse | null>(null);
  const [videoKeyframes, setVideoKeyframes] = useState<TaskVideoKeyframeExtractionResponse | null>(null);
  const [sandboxHtmlSnapshot, setSandboxHtmlSnapshot] = useState("");
  const [draftResult, setDraftResult] = useState<TaskDraftBuildResponse | null>(null);
  const [draftJson, setDraftJson] = useState("{\n  \"beauty_score\": \"1\",\n  \"motion_richness_score\": \"1\",\n  \"richness_reason\": \"平台 dry-run：待 AI 规则补齐后替换原因\"\n}");
  const [aiDraftJson, setAiDraftJson] = useState("{\n  \"beauty_score\": 1,\n  \"motion_richness_score\": 2,\n  \"richness_reason\": \"截图一般但视频有明确跳转动效，建议人工复核。\",\n  \"scene_consistency_score\": { \"product1\": 1, \"product2\": 1 },\n  \"scene_consistency_reason\": \"前后场景主体一致，动效补充了静态截图信息。\",\n  \"discard\": false,\n  \"check_remark\": \"AI草稿：仅暂存，人工复核后再决定。\"\n}");
  const [mediaJudgementJson, setMediaJudgementJson] = useState("{\n  \"image_judgement\": {\n    \"layout_normal\": true,\n    \"mojibake_or_broken_layout\": false,\n    \"reason\": \"左图完整，排版正常，无乱码。\"\n  },\n  \"video_keyframe_judgements\": [\n    {\n      \"resource_key\": \"motion_media_1\",\n      \"action_visible\": true,\n      \"matches_sandbox_trace\": true,\n      \"keyframe_summary\": \"关键帧看到点击后页面跳转。\",\n      \"reason\": \"产物一复现了沙箱点击跳转。\"\n    },\n    {\n      \"resource_key\": \"motion_media_2\",\n      \"action_visible\": true,\n      \"matches_sandbox_trace\": true,\n      \"keyframe_summary\": \"关键帧看到同样操作反馈。\",\n      \"reason\": \"产物二也复现了点击反馈。\"\n    }\n  ]\n}");
  const [providerPrompt, setProviderPrompt] = useState("按返修评分规则输出结构化 JSON；重点复核最终截图、视频动效和前后场景一致性。");
  const [reviewNote, setReviewNote] = useState("人工确认分数、原因、一致性和废弃判断可作为草稿暂存起点。");
  const [reviewApproval, setReviewApproval] = useState<TaskDraftReviewApprovalResponse | null>(null);
  const [lastRefresh, setLastRefresh] = useState<ProductionAccountRefreshResponse | null>(null);
  const [showOnlyPendingTasks, setShowOnlyPendingTasks] = useState(true);
  const [loading, setLoading] = useState(false);
  const [draftLoading, setDraftLoading] = useState(false);
  const [questionContextLoading, setQuestionContextLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [productionStatus, setProductionStatus] = useState<AutoProductionStatusResponse | null>(null);
  const [productionDevices, setProductionDevices] = useState<ExecutionDeviceItem[]>([]);
  const [productionForm, setProductionForm] = useState<StartProductionPayload>(() => defaultProductionForm());
  const [productionMaxMode, setProductionMaxMode] = useState<"limited">("limited");
  const [productionLoading, setProductionLoading] = useState(false);
  const [selectedAutoAccountIds, setSelectedAutoAccountIds] = useState<string[]>([]);
  const [previewAccountUserId, setPreviewAccountUserId] = useState("");
  const [autoRunLoading, setAutoRunLoading] = useState(false);
  const [noSubmitLoading, setNoSubmitLoading] = useState(false);
  const [autoRun, setAutoRun] = useState<TaskAutoRunResponse | null>(null);
  const [autoRunPreflight, setAutoRunPreflight] = useState<TaskAutoRunPreflightResponse | null>(null);
  const [autoRunWorkerStatus, setAutoRunWorkerStatus] = useState<TaskAutoRunWorkerStatusResponse | null>(null);
  const [noSubmitResult, setNoSubmitResult] = useState<UnifiedNoSubmitResult | null>(null);

  const loadCatalog = async () => {
    const response = await fetchTaskCatalog();
    setCatalog(response);
  };

  const loadProductionDashboard = async () => {
    setProductionDashboard(await fetchProductionDashboard());
  };

  const loadTaskAbilities = async () => {
    const response = await fetchTaskAbilityDrafts();
    setTaskAbilityDrafts(response.items);
  };

  const loadTaskWorkbench = async () => {
    await Promise.all([loadCatalog(), loadProductionDashboard(), loadTaskAbilities()]);
  };

  useEffect(() => {
    void loadTaskWorkbench();
  }, []);

  const openDetail = async (record: TaskCatalogItem) => {
    setLoading(true);
    try {
      setDraftResult(null);
      setReviewApproval(null);
      setQuestionContext(null);
      setOperationProcessPlan(null);
      setSandboxClickPlan(null);
      setMediaInspectionPlan(null);
      setMediaInspectionExecution(null);
      setMediaInspectionProvider(null);
      setVideoKeyframes(null);
      setSandboxHtmlSnapshot("");
      setNoSubmitResult(null);
      const nextDetail = await fetchTaskDetail(record.id);
      setDetail(nextDetail);
      const processPlanResponse = await fetchTaskOperationProcessPlan(record.id).catch(() => null);
      setOperationProcessPlan(processPlanResponse);
      if (!(record.capability_available || record.capability_recording_count > 0)) {
        setCapability(null);
        setQuestionContext(null);
        setDrawerOpen(true);
        return;
      }
      try {
        const [capabilityResponse, contextResponse] = await Promise.all([
          fetchTaskCapability(record.id),
          fetchTaskHttpQuestionContext(record.id),
        ]);
        setCapability(capabilityResponse);
        setQuestionContext(contextResponse);
      } catch {
        setCapability(null);
        setQuestionContext(null);
      }
      setDrawerOpen(true);
    } finally {
      setLoading(false);
    }
  };

  const parseDraftData = () => {
    try {
      const parsed = draftJson.trim() ? JSON.parse(draftJson) : {};
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        message.error("草稿字段必须是 JSON 对象");
        return null;
      }
      return parsed as Record<string, unknown>;
    } catch {
      message.error("草稿字段不是有效 JSON");
      return null;
    }
  };

  const parseAiDraftData = () => {
    try {
      const parsed = aiDraftJson.trim() ? JSON.parse(aiDraftJson) : {};
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        message.error("AI 输出必须是 JSON 对象");
        return null;
      }
      return parsed as Record<string, unknown>;
    } catch {
      message.error("AI 输出不是有效 JSON");
      return null;
    }
  };

  const parseMediaJudgementData = () => {
    try {
      const parsed = mediaJudgementJson.trim() ? JSON.parse(mediaJudgementJson) : {};
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
        message.error("媒体判断必须是 JSON 对象");
        return null;
      }
      return parsed as { image_judgement?: Record<string, unknown>; video_keyframe_judgements?: Record<string, unknown>[] };
    } catch {
      message.error("媒体判断不是有效 JSON");
      return null;
    }
  };

  const handleDraftDryRun = async () => {
    if (!detail) return;
    const answerData = parseDraftData();
    if (!answerData) return;
    setDraftLoading(true);
    try {
      const response = await buildTaskCapabilityDraft(detail.item.id, {
        answer_data: answerData,
        remark_marker: "PLATFORM_DRY_RUN",
        execute: false,
      });
      setDraftResult(response);
      setReviewApproval(null);
      message.success("已生成草稿暂存 dry-run，未触网");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "生成草稿 dry-run 失败");
    } finally {
      setDraftLoading(false);
    }
  };

  const handleAiDraftDryRun = async () => {
    if (!detail) return;
    const aiOutput = parseAiDraftData();
    if (!aiOutput) return;
    setDraftLoading(true);
    try {
      const response = await buildTaskCapabilityAiDraft(detail.item.id, {
        ai_output: aiOutput,
        remark_marker: "AI_SCHEMA_DRY_RUN",
        execute: false,
      });
      setDraftResult(response);
      setReviewApproval(null);
      message.success("已将 AI 输出映射为草稿 dry-run，未触网");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "AI 输出映射失败");
    } finally {
      setDraftLoading(false);
    }
  };

  const handleProviderDraftDryRun = async (useProvider: boolean) => {
    if (!detail) return;
    setDraftLoading(true);
    try {
      const response = await buildTaskCapabilityProviderDraft(detail.item.id, {
        use_provider: useProvider,
        operator_prompt: providerPrompt,
        execute: false,
      });
      setDraftResult(response);
      setReviewApproval(null);
      if (useProvider && response.blockers.length) {
        message.warning(response.message);
      } else {
        message.success(useProvider ? "上游做题 AI 已生成 dry-run 草稿" : "本地做题 AI 已生成 dry-run 草稿");
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "做题 AI 草稿生成失败");
    } finally {
      setDraftLoading(false);
    }
  };

  const handleDraftExecute = () => {
    if (!detail) return;
    const answerData = parseDraftData();
    if (!answerData) return;
    Modal.confirm({
      title: "确认只执行草稿暂存",
      content: "该动作只允许调用 SubmitTempItemAnswer 保存草稿，不会提交、继续下一题、放弃或领取。后端仍要求环境变量闸门开启。",
      okText: "执行草稿暂存",
      cancelText: "取消",
      onOk: async () => {
        setDraftLoading(true);
        try {
          const response = await buildTaskCapabilityDraft(detail.item.id, {
            answer_data: answerData,
            remark_marker: "PLATFORM_GATED_WRITE",
            execute: true,
            allow_draft_write: true,
          });
          setDraftResult(response);
          setReviewApproval(null);
          if (response.ok) {
            message.success("草稿暂存已执行，仍需人工打开页面复核");
          } else {
            message.warning(response.message);
          }
        } catch (error) {
          message.error(error instanceof Error ? error.message : "执行草稿暂存失败");
        } finally {
          setDraftLoading(false);
        }
      },
    });
  };

  const handleReviewApproval = async () => {
    if (!detail || !draftResult?.ai_review_preview) return;
    setDraftLoading(true);
    try {
      const response = await approveTaskCapabilityReview(detail.item.id, {
        ai_output: draftResult.ai_review_preview.ai_output,
        reviewer: "operator",
        review_note: reviewNote,
        write_audit: true,
      });
      setReviewApproval(response);
      message.success("已生成受控草稿暂存确认单，未触网");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "生成复核确认单失败");
    } finally {
      setDraftLoading(false);
    }
  };

  const handleLiveQuestionContextFetch = async () => {
    if (!detail) return;
    setQuestionContextLoading(true);
    try {
      const response = await fetchTaskHttpQuestionContext(detail.item.id, {
        prefer_live: true,
        allow_remote_fetch: true,
        account_user_id: detail.source_account_user_id,
      });
      setQuestionContext(response);
      if (response.sends_network && !response.writes_remote) {
        message.success("已用 MGetAnswerList 只读取题面上下文，未写远端");
      } else {
        message.info(response.message);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "MGetAnswerList 只读取题面失败");
    } finally {
      setQuestionContextLoading(false);
    }
  };

  const handleSandboxClickPlan = async () => {
    if (!detail || !questionContext) return;
    const webPage = questionContext.material_resources.find((item) => item.key === "web_page");
    setQuestionContextLoading(true);
    try {
      const response = await buildTaskSandboxClickPlan(detail.item.id, {
        html_url: webPage?.url ?? "",
        html_snapshot: sandboxHtmlSnapshot,
        allow_remote_fetch: false,
        max_candidates: 20,
      });
      setSandboxClickPlan(response);
      if (response.ok) {
        message.success(`已生成 ${response.click_candidates.length} 个沙箱点击候选，未执行点击`);
      } else {
        message.warning(response.message);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "生成沙箱点击计划失败");
    } finally {
      setQuestionContextLoading(false);
    }
  };

  const handleSandboxClickExecution = async () => {
    if (!detail || !questionContext || !sandboxClickPlan) return;
    const webPage = questionContext.material_resources.find((item) => item.key === "web_page");
    const selectors = sandboxClickPlan.click_candidates
      .filter((item) => item.risk !== "form-submit")
      .map((item) => item.selector)
      .slice(0, 3);
    if (!selectors.length) {
      message.warning("没有可执行的低风险点击候选");
      return;
    }
    setQuestionContextLoading(true);
    try {
      const response = await executeTaskSandboxClicks(detail.item.id, {
        html_url: webPage?.url ?? sandboxClickPlan.html_url,
        selectors,
        allow_execute: true,
        max_clicks: selectors.length,
        timeout_ms: 3000,
      });
      setSandboxClickExecution(response);
      if (response.ok) {
        message.success(`已执行 ${response.interaction_summary.clicked_count} 个沙箱点击，未写远端`);
      } else {
        message.warning(response.message);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "执行沙箱点击失败");
    } finally {
      setQuestionContextLoading(false);
    }
  };

  const handleSandboxClickDraft = async () => {
    if (!detail || !sandboxClickExecution) return;
    setDraftLoading(true);
    try {
      const response = await buildTaskSandboxClickDraft(detail.item.id, {
        click_results: sandboxClickExecution.click_results,
        interaction_summary: sandboxClickExecution.interaction_summary,
        web_accessible: sandboxClickExecution.ok,
        remark_marker: "SANDBOX_CLICK_DRY_RUN",
        write_audit: true,
      });
      setDraftResult(response);
      setReviewApproval(null);
      message.success("已把沙箱点击信号生成 dry-run 草稿，未写远端");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "生成沙箱 dry-run 草稿失败");
    } finally {
      setDraftLoading(false);
    }
  };

  const handleMediaInspectionPlan = async () => {
    if (!detail || !questionContext) return;
    const image = questionContext.material_resources.find((item) => item.key === "final_screenshot");
    const videos = questionContext.material_resources
      .filter((item) => item.material_type === "video")
      .map((item) => item.url);
    setQuestionContextLoading(true);
    try {
      const response = await buildTaskMediaInspectionPlan(detail.item.id, {
        image_url: image?.url ?? "",
        video_urls: videos,
        allow_remote_probe: false,
      });
      setMediaInspectionPlan(response);
      setMediaInspectionExecution(null);
      setMediaInspectionProvider(null);
      setVideoKeyframes(null);
      if (response.ok) {
        message.success("已生成媒体检查计划，未做视觉判分");
      } else {
        message.warning(response.message);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "生成媒体检查计划失败");
    } finally {
      setQuestionContextLoading(false);
    }
  };

  const handleMediaInspectionExecution = async () => {
    if (!detail || !mediaInspectionPlan) return;
    setQuestionContextLoading(true);
    try {
      const response = await executeTaskMediaInspection(detail.item.id, {
        media_resources: mediaInspectionPlan.media_resources,
        allow_remote_probe: true,
        max_bytes: 65536,
      });
      setMediaInspectionExecution(response);
      if (response.ok) {
        message.success("已完成媒体基础探测，仍未做视觉判分");
      } else {
        message.warning(response.message);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "执行媒体基础探测失败");
    } finally {
      setQuestionContextLoading(false);
    }
  };

  const handleVideoKeyframeExtraction = async (maxFrames = 3, reuseCachedFrames = true) => {
    if (!detail || !mediaInspectionPlan) return;
    setQuestionContextLoading(true);
    try {
      const response = await extractTaskVideoKeyframes(detail.item.id, {
        media_resources: mediaInspectionPlan.media_resources,
        allow_extract: true,
        archive_frames: true,
        reuse_cached_frames: reuseCachedFrames,
        cache_manifest_path: reuseCachedFrames ? videoKeyframes?.artifact_path ?? "" : "",
        max_frames_per_video: maxFrames,
        timeout_ms: maxFrames > 3 ? 20000 : 12000,
      });
      setVideoKeyframes(response);
      if (response.ok) {
        message.success(response.cache_hit ? "已复用本地多帧关键帧缓存" : "已抽取视频关键帧，仍未做视频判分");
      } else {
        message.warning(response.message);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "抽取视频关键帧失败");
    } finally {
      setQuestionContextLoading(false);
    }
  };

  const handleMediaInspectionProvider = async () => {
    if (!detail || !mediaInspectionPlan) return;
    setQuestionContextLoading(true);
    try {
      const response = await buildTaskMediaInspectionProvider(detail.item.id, {
        media_resources: mediaInspectionPlan.media_resources,
        video_keyframes: videoKeyframes?.keyframe_results ?? [],
        sandbox_trace: sandboxClickExecution ? { ...sandboxClickExecution.interaction_summary } : {},
        operator_prompt: "用左图判断排版是否正常；优先依据已抽取视频关键帧判断是否复现沙箱点击的跳转、交互或动效。低置信时补抽更多关键帧后重新判断。",
        use_provider: true,
        auto_supplement_low_confidence: true,
        supplement_max_frames_per_video: 5,
        write_audit: true,
      });
      setMediaInspectionProvider(response);
      setMediaJudgementJson(JSON.stringify(
        {
          image_judgement: response.image_judgement,
          video_keyframe_judgements: response.video_keyframe_judgements,
        },
        null,
        2,
      ));
      if (response.draft_preview) {
        setDraftResult(response.draft_preview);
        setReviewApproval(null);
      }
      if (response.ok) {
        message.success("做题 AI 已生成媒体判断和 dry-run 草稿预览，未写远端");
      } else {
        message.warning(response.message);
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : "调用做题 AI 生成媒体判断失败");
    } finally {
      setQuestionContextLoading(false);
    }
  };

  const handleMediaInspectionDraft = async () => {
    if (!detail) return;
    const judgement = parseMediaJudgementData();
    if (!judgement) return;
    setDraftLoading(true);
    try {
      const response = await buildTaskMediaInspectionDraft(detail.item.id, {
        ...judgement,
        remark_marker: "MEDIA_INSPECTION_DRY_RUN",
        write_audit: true,
      });
      setDraftResult(response);
      setReviewApproval(null);
      message.success("已把媒体判断生成 dry-run 草稿，未写远端");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "媒体判断生成 dry-run 草稿失败");
    } finally {
      setDraftLoading(false);
    }
  };

  const handleConfirmationExecute = () => {
    if (!detail || !reviewApproval) return;
    const sheet = reviewApproval.confirmation_sheet;
    Modal.confirm({
      title: "按确认单字段受控执行草稿暂存",
      content: (
        <Space direction="vertical" size="small">
          <Typography.Text>仅允许调用 {sheet.allowed_endpoint} 保存草稿，不会提交、继续下一题、放弃或领取。</Typography.Text>
          <Typography.Text>后端必须满足：{sheet.required_gates.join("；")}。</Typography.Text>
          <Typography.Text type="warning">确认文本：{sheet.confirm_text}</Typography.Text>
        </Space>
      ),
      okText: "按确认单执行草稿暂存",
      cancelText: "取消",
      onOk: async () => {
        setDraftLoading(true);
        try {
          const response = await buildTaskCapabilityDraft(detail.item.id, {
            answer_data: sheet.mapped_answer_data,
            remark_marker: "AI_REVIEW_APPROVED_GATED_WRITE",
            execute: true,
            allow_draft_write: true,
          });
          setDraftResult(response);
          if (response.ok) {
            message.success("已按确认单字段执行草稿暂存，仍需人工打开页面复核");
          } else {
            message.warning(response.message);
          }
        } catch (error) {
          message.error(error instanceof Error ? error.message : "按确认单执行草稿暂存失败");
        } finally {
          setDraftLoading(false);
        }
      },
    });
  };

  const handleRefresh = async () => {
    setLoading(true);
    try {
      const result = await refreshProductionAccounts();
      setLastRefresh(result);
      const feedback = result.failed_count > 0 ? message.warning : message.success;
      feedback(result.message || `刷新完成：成功 ${result.refreshed_count} 个，失败 ${result.failed_count} 个`);
      await loadTaskWorkbench();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "刷新生产数据失败");
    } finally {
      setLoading(false);
    }
  };

  const dedupedTasks = useMemo(() => dedupeTaskCatalogItems(catalog?.items ?? []), [catalog]);
  const taskQueues = useMemo(() => buildTaskQueues(productionDashboard?.accounts ?? [], catalog?.items ?? []), [productionDashboard, catalog]);
  const visibleTasks = useMemo(
    () => showOnlyPendingTasks ? dedupedTasks.filter((item) => !item.last_task_page_error && parsePendingCount(item.pending_raw) > 0) : dedupedTasks,
    [dedupedTasks, showOnlyPendingTasks],
  );
  const hiddenDuplicateCount = Math.max((catalog?.items.length ?? 0) - dedupedTasks.length, 0);
  const pendingTaskCount = dedupedTasks.filter((item) => !item.last_task_page_error && parsePendingCount(item.pending_raw) > 0).length;
  const pendingTotal = dedupedTasks.reduce((total, item) => total + (item.last_task_page_error ? 0 : parsePendingCount(item.pending_raw)), 0);
  const selectedTaskQueue = taskQueues.find((item) => item.key === selectedTaskQueueKey) ?? taskQueues[0] ?? null;
  const abilityDraftByTaskId = useMemo(() => {
    const result = new Map<string, TaskAbilityDraftItem>();
    for (const draft of taskAbilityDrafts) {
      if (draft.task_id && !result.has(draft.task_id)) result.set(draft.task_id, draft);
    }
    return result;
  }, [taskAbilityDrafts]);
  const taskAbilityDraftFor = (queue: TaskQueueRow | null | undefined) => (
    queue?.task_id ? abilityDraftByTaskId.get(queue.task_id) ?? null : null
  );
  const hasTaskCapability = (queue: TaskQueueRow | null | undefined) => Boolean(taskAbilityDraftFor(queue)?.capability_enabled);
  const canOpenTaskOperation = (queue: TaskQueueRow | null | undefined) => Boolean(queue?.catalog_item);
  const selectedTaskAbilityDraft = taskAbilityDraftFor(selectedTaskQueue);
  const taskIsBon8 = (taskId: string | null | undefined) => taskId === BON8_TASK_ID;
  const queueExecutableReady = (queue: TaskQueueRow | null | undefined) => hasTaskCapability(queue);
  const taskHasCurrentQuestion = (row: TaskQueueAccountRow) => row.task.pending > 0 || row.task.processing > 0 || row.task.repair > 0;
  const taskHasRunnableAutoQuestion = (row: TaskQueueAccountRow) => (taskIsBon8(row.task.task_id) ? (row.task.processing > 0 || row.task.repair > 0) : taskHasCurrentQuestion(row));
  const taskCanAutoReceive = (row: TaskQueueAccountRow) => row.task.auto_receive_ready;
  const taskAutoReceiveReason = (row: TaskQueueAccountRow) => {
    if (!taskHasCurrentQuestion(row)) return "当前无题，不进入自动循环";
    if (taskIsBon8(row.task.task_id) && !taskHasRunnableAutoQuestion(row)) return "bon8 当前只跑已领取的处理中/返修题，pending-only 账号暂不进入通用自动做题";
    if (taskCanAutoReceive(row) && (row.task.processing > 0 || row.task.repair > 0)) return "当前已有处理中/返修题，可直接继续自动做题";
    return row.task.auto_receive_block_reason || "已满足自动领题条件";
  };
  const taskAutoRunEligibilityLabel = (row: TaskQueueAccountRow) => {
    if (!taskCanAutoReceive(row)) return "不可进入自动循环";
    if (row.task.processing > 0 || row.task.repair > 0) return "可继续自动做题";
    return "可自动领题并做题";
  };
  const taskAccountRows = selectedTaskQueue?.accounts ?? [];
  const isBon8Task = detail?.item.task_id === BON8_TASK_ID || selectedTaskQueue?.task_id === BON8_TASK_ID;
  const isResearchChartTask = RESEARCH_CHART_TASK_IDS.has(detail?.item.task_id ?? "") || RESEARCH_CHART_TASK_IDS.has(selectedTaskQueue?.task_id ?? "");
  const runnableTaskAccountRows = taskAccountRows.filter((row) => row.account.cookie_synced && row.account.status !== "disabled" && taskHasRunnableAutoQuestion(row) && taskCanAutoReceive(row));
  const noSubmitAccountRows = taskAccountRows.filter((row) => row.account.cookie_synced && row.account.status !== "disabled" && (isBon8Task ? taskHasRunnableAutoQuestion(row) : taskHasCurrentQuestion(row)));
  const blockedAutoReceiveRows = taskAccountRows.filter((row) => row.account.cookie_synced && row.account.status !== "disabled" && taskHasCurrentQuestion(row) && !taskCanAutoReceive(row));
  const runnableTaskAccountIds = runnableTaskAccountRows.map((row) => row.account.user_id);

  useEffect(() => {
    if (!selectedTaskQueueKey && taskQueues.length) {
      setSelectedTaskQueueKey(taskQueues[0].key);
      return;
    }
    if (selectedTaskQueueKey && taskQueues.length && !taskQueues.some((item) => item.key === selectedTaskQueueKey)) {
      setSelectedTaskQueueKey(taskQueues[0].key);
    }
  }, [taskQueues, selectedTaskQueueKey]);

  useEffect(() => {
    setSelectedAutoAccountIds((current) => {
      const stillVisible = current.filter((userId) => taskAccountRows.some((row) => row.account.user_id === userId));
      return stillVisible.length ? stillVisible : runnableTaskAccountIds;
    });
  }, [selectedTaskQueue?.key, runnableTaskAccountIds.join("|")]);

  useEffect(() => {
    setPreviewAccountUserId((current) => {
      const visibleIds = noSubmitAccountRows.map((row) => row.account.user_id);
      if (current && visibleIds.includes(current)) return current;
      return visibleIds[0] ?? "";
    });
  }, [selectedTaskQueue?.key, noSubmitAccountRows.map((row) => row.account.user_id).join("|")]);

  useEffect(() => {
    const taskId = selectedTaskQueue?.task_id;
    if (!taskId) {
      setAutoRun(null);
      setAutoRunWorkerStatus(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const activeRun = await fetchActiveTaskAutoRun(taskId, taskAccountRows.map((row) => row.account.user_id));
        if (cancelled) return;
        if (!activeRun) {
          setAutoRun(null);
          setAutoRunWorkerStatus(null);
          return;
        }
        setAutoRun(activeRun);
        setAutoRunWorkerStatus(await fetchTaskAutoRunWorkerStatus(activeRun.run_id).catch(() => null));
      } catch {
        if (!cancelled) {
          setAutoRun(null);
          setAutoRunWorkerStatus(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedTaskQueue?.task_id, taskAccountRows.map((row) => row.account.user_id).join("|")]);

  const openTaskCapabilityDetail = async (queue: TaskQueueRow | null = selectedTaskQueue) => {
    if (!queue?.catalog_item) {
      message.warning("这个任务还没有目录记录，先刷新生产数据或补充任务目录。");
      return;
    }
    setProductionForm(defaultProductionForm());
    setProductionMaxMode("limited");
    setProductionLoading(true);
    await openDetail(queue.catalog_item);
    try {
      const [status, devices] = await Promise.all([
        fetchTaskAutoProductionStatus(queue.task_id),
        fetchExecutionDevicesForProduction(),
      ]);
      setProductionStatus(status);
      setProductionDevices(devices.items);
    } catch (error: unknown) {
      message.warning(error instanceof Error ? error.message : "生产控制状态加载失败");
      setProductionStatus(null);
      setProductionDevices([]);
    } finally {
      setProductionLoading(false);
    }
  };

  const updateProductionForm = (patch: Partial<StartProductionPayload>) => {
    setProductionForm((current) => ({ ...current, ...patch }));
  };

  const pauseProductionRun = async () => {
    if (!autoRun?.run_id) return;
    setProductionLoading(true);
    try {
      const result = await pauseAutoAnswerRun(autoRun.run_id);
      setAutoRun(result);
      message.success("生产已暂停");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "暂停生产失败");
    } finally {
      setProductionLoading(false);
    }
  };

  const resumeProductionRun = async () => {
    if (!autoRun?.run_id) return;
    setProductionLoading(true);
    try {
      const result = await resumeAutoAnswerRun(autoRun.run_id);
      setAutoRun(result);
      message.success("生产已恢复");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "恢复生产失败");
    } finally {
      setProductionLoading(false);
    }
  };

  const stopProductionRun = async () => {
    if (!autoRun?.run_id) return;
    setProductionLoading(true);
    try {
      const result = await stopAutoAnswerRun(autoRun.run_id);
      setAutoRun(result);
      message.success("生产已停止");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "停止生产失败");
    } finally {
      setProductionLoading(false);
    }
  };

  const openAccountWindow = async (account: ProductionAccountCard, target: "task" | "personal") => {
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    try {
      const result = await openAccountTarget(account.user_id, target);
      if (popup) {
        popup.location.replace(result.open_url);
      } else {
        window.open(result.open_url, "_blank", "noopener,noreferrer");
      }
      message.success(result.message);
    } catch (error: unknown) {
      popup?.close();
      message.error(error instanceof Error ? error.message : "打开账号窗口失败");
    }
  };

  const openAccountTaskPage = (account: ProductionAccountCard) => {
    void openAccountWindow(account, "task");
  };

  const openAccountPersonalCenter = (account: ProductionAccountCard) => {
    void openAccountWindow(account, "personal");
  };

  const refreshAutoRun = async (runId = autoRun?.run_id) => {
    if (!runId) return;
    const result = await fetchTaskAutoRun(runId);
    setAutoRun(result);
    setAutoRunWorkerStatus(await fetchTaskAutoRunWorkerStatus(runId).catch(() => null));
  };

  const runAutoTaskPreflight = async () => {
    const taskId = detail?.item.task_id || selectedTaskQueue?.task_id;
    if (!taskId) {
      message.warning("当前任务缺少 TaskID，不能执行启动前自检。");
      return;
    }
    const selectedRunnable = selectedAutoAccountIds.filter((userId) => runnableTaskAccountIds.includes(userId));
    setAutoRunLoading(true);
    try {
      const result = await checkTaskAutoRunPreflight({
        account_user_ids: selectedRunnable,
        task_id: taskId,
        node_id: "1",
        ability_version: detailAbilityDraft?.version ?? "",
        write_audit: false,
      });
      setAutoRunPreflight(result);
      if (result.can_start) {
        message.success(result.message);
      } else {
        message.warning(result.message);
      }
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "启动前自检失败");
    } finally {
      setAutoRunLoading(false);
    }
  };

  const startAutoTaskWorker = async () => {
    if (!autoRun?.run_id) return;
    setAutoRunLoading(true);
    try {
      const worker = await startTaskAutoRunWorker(autoRun.run_id);
      setAutoRunWorkerStatus(worker);
      message.success("后台自动做题循环已启动");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "后台循环启动失败");
    } finally {
      setAutoRunLoading(false);
    }
  };

  const stopAutoTaskRun = async () => {
    if (!autoRun?.run_id) return;
    setAutoRunLoading(true);
    try {
      await stopTaskAutoRunWorker(autoRun.run_id).catch(() => null);
      const result = await stopTaskAutoRun(autoRun.run_id);
      setAutoRun(result);
      setAutoRunWorkerStatus(await fetchTaskAutoRunWorkerStatus(autoRun.run_id).catch(() => null));
      message.success("AI 自动做题已立即停止");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "停止 AI 自动做题失败");
    } finally {
      setAutoRunLoading(false);
    }
  };

  const buildAbilityNoSubmitResult = (result: TaskAbilityRealNoSubmitResponse): UnifiedNoSubmitResult => ({
    task_kind: "generic",
    stage: result.stage,
    review_status: result.review_status,
    account_user_id: String(result.queue_snapshot?.account_user_id ?? previewAccountUserId),
    item_id: String(result.question_context?.item_id ?? ""),
    sends_network: result.sends_network,
    writes_remote: result.writes_remote,
    submits_remote: result.submits_remote,
    saved_to_task_ui: result.saved_to_task_ui,
    answer_preview: result.saved_answer ?? result.answer_preview ?? {},
    temp_result: result.temp_draft_result ?? {},
    verify_result: {},
    review_artifact_path: result.review_artifact_path,
    message: result.message,
    ui_review_hint: result.ui_review_hint,
  });

  const handleTaskEndToEndNoSubmit = async () => {
    const taskId = detail?.item.task_id || selectedTaskQueue?.task_id;
    if (!taskId) {
      message.warning("当前任务缺少 TaskID，不能执行端到端做题不提交。");
      return;
    }
    if (isBon8Task) {
      message.warning("bon8 旧任务页不再执行端到端不提交，请进入能力工作台 Step3 审核流程。");
      return;
    }
    if (!previewAccountUserId) {
      message.warning("请先选择一个预览账号。");
      return;
    }
    setNoSubmitLoading(true);
    try {
      if (!detailAbilityDraft) {
        throw new Error("当前任务还没有题型能力草稿，不能执行端到端做题不提交。");
      }
      const result = await runTaskAbilityRealNoSubmit(detailAbilityDraft.id, {
        account_user_id: previewAccountUserId,
        use_system_ai_for_vision: false,
      });
      setNoSubmitResult(buildAbilityNoSubmitResult(result));
      message.success(result.saved_to_task_ui ? "端到端做题不提交已保存到真实做题界面，请核对结果。" : result.message);
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "端到端做题不提交执行失败");
    } finally {
      setNoSubmitLoading(false);
    }
  };

  const toggleTaskQueueAccounts = (record: TaskQueueRow) => {
    setSelectedTaskQueueKey(record.key);
    setExpandedTaskQueueKeys((current) => (
      current.includes(record.key) ? [] : [record.key]
    ));
  };

  const taskQueueColumns: ColumnsType<TaskQueueRow> = [
    { title: "任务队列", key: "task", ellipsis: true, fixed: "left", width: 360, render: (_, record) => (
      <Space direction="vertical" size={0}>
        <Typography.Text strong ellipsis>{record.task_name}</Typography.Text>
        <Typography.Text type="secondary" ellipsis>{record.task_id || record.key}</Typography.Text>
      </Space>
    ) },
    queueNumberColumn("账号数", "account_count"),
    queueNumberColumn("待处理", "pending"),
    queueNumberColumn("处理中", "processing"),
    queueNumberColumn("进行中", "in_progress"),
    queueNumberColumn("返修", "repair"),
    queueNumberColumn("已交付", "delivered"),
    queueNumberColumn("废弃", "abandoned"),
    { title: "状态", key: "state", width: 180, render: (_, record) => (
      <Space wrap>
        {(() => {
          const label = abilityFlowLabel(taskAbilityDraftFor(record), Boolean(record.catalog_item?.capability_available));
          return <Tag color={label.color}>{label.text}</Tag>;
        })()}
        {record.catalog_item ? <Tag color="blue">任务目录</Tag> : null}
        {record.stale_count ? <Tag color="orange">过期 {record.stale_count}</Tag> : null}
        {record.error_count ? <Tag color="red">错误 {record.error_count}</Tag> : null}
      </Space>
    ) },
    { title: "操作", key: "action", width: 180, render: (_, record) => (
      <Space>
        <Button size="small" type="primary" onClick={(event) => { event.stopPropagation(); toggleTaskQueueAccounts(record); }}>
          {expandedTaskQueueKeys.includes(record.key) ? "收起账号分布" : "展开账号分布"}
        </Button>
        <Button size="small" disabled={!canOpenTaskOperation(record)} onClick={(event) => { event.stopPropagation(); void openTaskCapabilityDetail(record); }}>生产控制</Button>
      </Space>
    ) },
  ];

  const accountDistributionColumns: ColumnsType<TaskQueueAccountRow> = [
    { title: "账号", key: "account", ellipsis: true, render: (_, record) => (
      <Space direction="vertical" size={0}>
        <Typography.Text strong ellipsis>{record.account.custom_name || record.account.display_name}</Typography.Text>
        <Typography.Text type="secondary" ellipsis>{record.account.user_id}</Typography.Text>
      </Space>
    ) },
    { title: "待处理", dataIndex: ["task", "pending"], key: "pending", sorter: (left, right) => left.task.pending - right.task.pending },
    { title: "处理中", dataIndex: ["task", "processing"], key: "processing", sorter: (left, right) => left.task.processing - right.task.processing },
    { title: "进行中", dataIndex: ["task", "in_progress"], key: "in_progress" },
    { title: "返修", dataIndex: ["task", "repair"], key: "repair", sorter: (left, right) => left.task.repair - right.task.repair },
    { title: "已交付", dataIndex: ["task", "delivered"], key: "delivered" },
    { title: "已废弃", dataIndex: ["task", "abandoned"], key: "abandoned" },
    { title: "状态", key: "state", render: (_, record) => (
      <Space wrap>
        <Tag color={record.account.cookie_synced ? "green" : "gold"}>{record.account.cookie_synced ? "Cookie正常" : "需登录"}</Tag>
        {record.task.stale ? <Tag color="orange">数据过期</Tag> : null}
        {record.task.error ? <Tag color="red">任务错误</Tag> : null}
      </Space>
    ) },
    { title: "操作", key: "action", render: (_, record) => (
      <Space>
        <Button size="small" onClick={() => openAccountTaskPage(record.account)}>打开任务页</Button>
        <Button size="small" onClick={() => openAccountPersonalCenter(record.account)}>个人中心</Button>
      </Space>
    ) },
  ];

  const taskQueueAccountDetailColumns: ColumnsType<TaskQueueAccountRow> = [
    { title: "账号", key: "account", ellipsis: true, fixed: "left", width: 512, render: (_, record) => (
      <Space direction="vertical" size={0}>
        <Typography.Text strong ellipsis>{record.account.custom_name || record.account.display_name}</Typography.Text>
        <Typography.Text type="secondary" ellipsis>{record.account.user_id}</Typography.Text>
      </Space>
    ) },
    accountDetailNumberColumn("处理中", "processing"),
    accountDetailNumberColumn("进行中", "in_progress"),
    accountDetailNumberColumn("返修", "repair"),
    accountDetailNumberColumn("已交付", "delivered"),
    accountDetailNumberColumn("废弃", "abandoned"),
    { title: "状态", key: "state", width: 180, render: (_, record) => (
      <Space wrap>
        <Tag color={record.account.cookie_synced ? "green" : "gold"}>{record.account.cookie_synced ? "Cookie正常" : "需登录"}</Tag>
        {record.task.stale ? <Tag color="orange">数据过期</Tag> : null}
        {record.task.error ? <Tag color="red">任务错误</Tag> : null}
      </Space>
    ) },
    { title: "操作", key: "action", width: 180, render: (_, record) => (
      <Space>
        <Button size="small" onClick={() => openAccountTaskPage(record.account)}>打开任务页</Button>
        <Button size="small" onClick={() => openAccountPersonalCenter(record.account)}>个人中心</Button>
      </Space>
    ) },
  ];

  const columns: ColumnsType<TaskCatalogItem> = [
    { title: "任务", key: "task", ellipsis: true, render: (_, record) => (
      <Space direction="vertical" size={0}>
        <Typography.Text strong ellipsis>{record.task_short_name || record.raw_task_name || record.task_name_id}</Typography.Text>
        <Typography.Text type="secondary" ellipsis>{record.task_name_id}</Typography.Text>
      </Space>
    ) },
    { title: "任务状态", dataIndex: "task_status_raw", key: "task_status_raw", render: (value: string, record) => <Tag color={statusColorMap[record.task_status_color]}>{value}</Tag> },
    { title: "待处理", dataIndex: "pending_raw", key: "pending_raw", render: renderPending },
    { title: "来源账号", dataIndex: "source_account_user_id", key: "source_account_user_id" },
    { title: "最近采集", dataIndex: "last_task_page_seen_at", key: "last_task_page_seen_at", render: (value: string | null) => value ? new Date(value).toLocaleString() : "-" },
    { title: "操作", key: "action", render: (_, record) => (
      <Space>
        <Button size="small" type="primary" onClick={() => void openDetail(record)}>生产控制</Button>
        {(() => {
          const label = abilityFlowLabel(abilityDraftByTaskId.get(record.task_id), record.capability_available);
          return <Tag color={label.color}>{label.text}</Tag>;
        })()}
      </Space>
    ) },
  ];

  const fieldColumns: ColumnsType<TaskCapabilityFieldMapping> = [
    { title: "字段", dataIndex: "field", key: "field" },
    { title: "作用", dataIndex: "role", key: "role" },
    { title: "路径", dataIndex: "path", key: "path", ellipsis: true },
    { title: "当前值", dataIndex: "current_value", key: "current_value", ellipsis: true, render: renderJsonValue },
    { title: "同步 dataMap", dataIndex: "mirrored_in_data_map", key: "mirrored_in_data_map", render: (value: boolean) => <Tag color={value ? "green" : "gold"}>{value ? "是" : "否"}</Tag> },
  ];

  const ruleColumns: ColumnsType<TaskCapabilityRule> = [
    { title: "规则", dataIndex: "title", key: "title" },
    { title: "说明", dataIndex: "description", key: "description" },
    { title: "可选值", dataIndex: "values", key: "values", render: (values: string[]) => values.length ? values.join(" / ") : "-" },
  ];

  const inputSpecColumns: ColumnsType<TaskCapabilityInputSpec> = [
    { title: "材料", dataIndex: "title", key: "title" },
    { title: "类型", dataIndex: "material_type", key: "material_type", render: (value: string) => <Tag>{value}</Tag> },
    { title: "必需", dataIndex: "required", key: "required", render: (value: boolean) => <Tag color={value ? "red" : "default"}>{value ? "是" : "否"}</Tag> },
    { title: "用途", dataIndex: "usage", key: "usage" },
    { title: "复核点", dataIndex: "review_check", key: "review_check" },
    { title: "来源", dataIndex: "source", key: "source", ellipsis: true, render: (value: string) => value ? <Typography.Text copyable>{value}</Typography.Text> : "-" },
  ];

  const materialResourceColumns: ColumnsType<TaskQuestionMaterialResource> = [
    { title: "材料", dataIndex: "title", key: "title" },
    { title: "类型", dataIndex: "material_type", key: "material_type", render: (value: string) => <Tag>{value}</Tag> },
    { title: "用途", dataIndex: "purpose", key: "purpose" },
    { title: "URL", dataIndex: "url", key: "url", ellipsis: true, render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
  ];

  const decisionPipelineColumns: ColumnsType<TaskQuestionDecisionStep> = [
    { title: "步骤", dataIndex: "title", key: "title" },
    { title: "执行器", dataIndex: "executor", key: "executor", render: (value: string) => <Tag>{value}</Tag> },
    { title: "状态", dataIndex: "status", key: "status", render: renderChecklistStatus },
    { title: "输入", dataIndex: "input_keys", key: "input_keys", render: (values: string[]) => values.length ? values.join(" / ") : "-" },
    { title: "输出", dataIndex: "output_keys", key: "output_keys", render: (values: string[]) => values.join(" / ") },
  ];

  const iterationCandidateColumns: ColumnsType<TaskQuestionIterationCandidate> = [
    { title: "方向", dataIndex: "title", key: "title" },
    { title: "价值", dataIndex: "value", key: "value" },
    { title: "风险/约束", dataIndex: "risk", key: "risk" },
  ];

  const sandboxCandidateColumns: ColumnsType<TaskSandboxClickCandidate> = [
    { title: "选择器", dataIndex: "selector", key: "selector", render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
    { title: "标签", dataIndex: "tag", key: "tag", render: (value: string) => <Tag>{value}</Tag> },
    { title: "原因", dataIndex: "reason", key: "reason" },
    { title: "文本", dataIndex: "text", key: "text", ellipsis: true },
    { title: "风险", dataIndex: "risk", key: "risk", render: (value: string) => <Tag color={value === "low" ? "green" : "gold"}>{value}</Tag> },
  ];

  const sandboxStepColumns: ColumnsType<TaskSandboxClickPlanStep> = [
    { title: "步骤", dataIndex: "title", key: "title" },
    { title: "状态", dataIndex: "status", key: "status", render: renderChecklistStatus },
    { title: "说明", dataIndex: "detail", key: "detail" },
  ];

  const sandboxExecutionColumns: ColumnsType<TaskSandboxClickExecutionResult> = [
    { title: "选择器", dataIndex: "selector", key: "selector", ellipsis: true, render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={value === "clicked" ? "green" : "gold"}>{value}</Tag> },
    { title: "跳转", dataIndex: "url_changed", key: "url_changed", render: (value: boolean) => <Tag color={value ? "green" : "default"}>{value ? "是" : "否"}</Tag> },
    { title: "交互", dataIndex: "dom_changed", key: "dom_changed", render: (value: boolean) => <Tag color={value ? "green" : "default"}>{value ? "是" : "否"}</Tag> },
    { title: "动效", dataIndex: "animation_detected", key: "animation_detected", render: (value: boolean) => <Tag color={value ? "green" : "default"}>{value ? "是" : "否"}</Tag> },
    { title: "弹窗", dataIndex: "popup_detected", key: "popup_detected", render: (value: boolean) => <Tag color={value ? "green" : "default"}>{value ? "是" : "否"}</Tag> },
    { title: "证据", dataIndex: "evidence", key: "evidence", ellipsis: true },
  ];

  const mediaResourceColumns: ColumnsType<TaskMediaResource> = [
    { title: "资源", dataIndex: "title", key: "title" },
    { title: "类型", dataIndex: "material_type", key: "material_type", render: (value: string) => <Tag>{value}</Tag> },
    { title: "预期输出", dataIndex: "expected_output", key: "expected_output", render: (values: string[]) => values.join(" / ") },
    { title: "URL", dataIndex: "url", key: "url", ellipsis: true, render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
  ];

  const mediaStepColumns: ColumnsType<TaskMediaInspectionStep> = [
    { title: "步骤", dataIndex: "title", key: "title" },
    { title: "执行器", dataIndex: "executor", key: "executor", render: (value: string) => <Tag>{value}</Tag> },
    { title: "状态", dataIndex: "status", key: "status", render: renderChecklistStatus },
    { title: "说明", dataIndex: "detail", key: "detail" },
  ];

  const mediaProbeColumns: ColumnsType<TaskMediaProbeResult> = [
    { title: "资源", dataIndex: "title", key: "title" },
    { title: "类型", dataIndex: "material_type", key: "material_type", render: (value: string) => <Tag>{value}</Tag> },
    { title: "访问", dataIndex: "ok", key: "ok", render: (value: boolean) => <Tag color={value ? "green" : "red"}>{value ? "可访问" : "失败"}</Tag> },
    { title: "HTTP", dataIndex: "status_code", key: "status_code", render: renderJsonValue },
    { title: "Content-Type", dataIndex: "content_type", key: "content_type", render: renderJsonValue },
    { title: "大小", dataIndex: "content_length", key: "content_length", render: renderJsonValue },
    { title: "已取字节", dataIndex: "fetched_bytes", key: "fetched_bytes" },
    { title: "尺寸", key: "dimensions", render: (_, record) => (record.width && record.height ? `${record.width} x ${record.height}` : "-") },
    { title: "错误", dataIndex: "error", key: "error", render: renderJsonValue },
  ];

  const reviewColumns: ColumnsType<TaskDraftReviewItem> = [
    { title: "复核项", dataIndex: "title", key: "title" },
    { title: "AI值", dataIndex: "value", key: "value", render: renderJsonValue },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={value === "needs_review" ? "gold" : "default"}>{value}</Tag> },
    { title: "复核提示", dataIndex: "review_hint", key: "review_hint" },
  ];

  const gateColumns: ColumnsType<TaskDraftConfirmationGateStatus> = [
    { title: "闸门", dataIndex: "title", key: "title" },
    { title: "状态", dataIndex: "status", key: "status", render: (_: string, record) => <Tag color={record.passed ? "green" : "red"}>{record.passed ? "通过" : "阻塞"}</Tag> },
    { title: "说明", dataIndex: "detail", key: "detail" },
    { title: "下一步", dataIndex: "next_step", key: "next_step" },
  ];

  const fieldDiffColumns: ColumnsType<TaskDraftConfirmationFieldDiff> = [
    { title: "字段", dataIndex: "field", key: "field" },
    { title: "作用", dataIndex: "role", key: "role" },
    { title: "当前草稿", dataIndex: "current_value", key: "current_value", render: renderJsonValue },
    { title: "即将写入", dataIndex: "next_value", key: "next_value", render: renderJsonValue },
    { title: "变化", dataIndex: "changed", key: "changed", render: (value: boolean) => <Tag color={value ? "gold" : "default"}>{value ? "会变更" : "不变"}</Tag> },
    { title: "路径", dataIndex: "source_path", key: "source_path", ellipsis: true },
  ];

  const rehearsalChecklistColumns: ColumnsType<TaskDraftRehearsalChecklistItem> = [
    { title: "检查项", dataIndex: "title", key: "title" },
    { title: "状态", dataIndex: "status", key: "status", render: renderChecklistStatus },
    { title: "必需", dataIndex: "required", key: "required", render: (value: boolean) => <Tag color={value ? "red" : "default"}>{value ? "是" : "否"}</Tag> },
    { title: "说明", dataIndex: "detail", key: "detail" },
    { title: "下一步", dataIndex: "next_step", key: "next_step" },
  ];

  const outputSchemaColumns: ColumnsType<TaskCapabilityOutputField> = [
    { title: "AI字段", dataIndex: "field", key: "field" },
    { title: "类型", dataIndex: "type", key: "type" },
    { title: "必填", dataIndex: "required", key: "required", render: (value: boolean) => <Tag color={value ? "red" : "default"}>{value ? "是" : "否"}</Tag> },
    { title: "取值", dataIndex: "allowed_values", key: "allowed_values", render: (values: string[]) => values.length ? values.join(" / ") : "-" },
    { title: "映射", dataIndex: "maps_to", key: "maps_to", ellipsis: true, render: (values: string[]) => values.join("；") },
  ];

  const detailAbilityDraft = detail?.item.task_id ? abilityDraftByTaskId.get(detail.item.task_id) ?? null : null;
  const detailAbilityLabel = abilityFlowLabel(detailAbilityDraft, Boolean(capability));
  const autoAbilityReady = Boolean(detailAbilityDraft?.capability_enabled);
  const drawerCanUseAnswerCapability = Boolean(detailAbilityDraft?.capability_enabled || detailAbilityDraft?.flow_stage === "real_no_submit_ready" || detailAbilityDraft?.flow_stage === "real_no_submit_review");
  const autoRunHealthy = autoRun ? autoRun.health_ok : true;
  const autoRunWaitingForItems = autoAbilityReady && runnableTaskAccountIds.length === 0;
  const blockedAutoReceiveSummary = blockedAutoReceiveRows.map((row) => `${row.account.custom_name || row.account.display_name || row.account.user_id}：${taskAutoReceiveReason(row)}`).join("；");
  const autoRunEvidenceRows = useMemo(() => buildTaskAutoRunEvidenceRows(autoRun), [autoRun]);
  const bon8AdvancedRun = useMemo(() => (
    isBon8Task && autoRun?.adapter_key === "bon8" ? autoRun.raw_adapter_run as unknown as Bon8ProductionRunResponse : null
  ), [autoRun, isBon8Task]);
  const previewAccountLabel = previewAccountUserId
    ? (taskAccountRows.find((row) => row.account.user_id === previewAccountUserId)?.account.custom_name
      || taskAccountRows.find((row) => row.account.user_id === previewAccountUserId)?.account.display_name
      || previewAccountUserId)
    : "未选择";
  const autoAccountColumns: ColumnsType<TaskQueueAccountRow> = [
    { title: "账号", key: "account", render: (_, record) => (
      <Space direction="vertical" size={0}>
        <Typography.Text strong>{record.account.custom_name || record.account.display_name || record.account.user_id}</Typography.Text>
        <Typography.Text type="secondary">{record.account.user_id}</Typography.Text>
      </Space>
    ) },
    { title: "当前题", key: "question", render: (_, record) => (
      <Space wrap>
        {taskHasCurrentQuestion(record) ? <Tag color="green">有题</Tag> : <Tag color="default">无题跳过</Tag>}
        <Tag>待处理 {record.task.pending}</Tag>
        <Tag>处理中 {record.task.processing}</Tag>
        <Tag>返修 {record.task.repair}</Tag>
      </Space>
    ) },
    { title: "登录", key: "login", render: (_, record) => <Tag color={record.account.cookie_synced ? "green" : "gold"}>{record.account.cookie_synced ? "可执行" : "需登录"}</Tag> },
    { title: "自动循环资格", key: "auto_receive", render: (_, record) => (
      taskHasCurrentQuestion(record) ? (
        <Space direction="vertical" size={0}>
          <Tag color={taskCanAutoReceive(record) ? "green" : "red"}>{taskAutoRunEligibilityLabel(record)}</Tag>
          <Typography.Text type={taskCanAutoReceive(record) ? "secondary" : "warning"}>{taskAutoReceiveReason(record)}</Typography.Text>
        </Space>
      ) : (
        <Typography.Text type="secondary">当前无题，不进入自动循环</Typography.Text>
      )
    ) },
  ];
  const autoRunAccountColumns: ColumnsType<TaskAutoRunAccountState> = [
    { title: "账号", dataIndex: "account_name", key: "account_name", render: (value: string, record) => value || record.account_user_id },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string, record) => (
      <Space wrap>
        <Tag color={!record.healthy ? "red" : value === "stopped" ? "default" : "green"}>{record.healthy ? "正常" : "异常"}</Tag>
        <Typography.Text type="secondary">{value}</Typography.Text>
      </Space>
    ) },
    { title: "当前题", dataIndex: "current_item_id", key: "current_item_id", render: (value: string) => value || "-" },
    { title: "阶段", dataIndex: "current_stage", key: "current_stage", render: (value: string) => value || "-" },
    { title: "最后错误", dataIndex: "last_error", key: "last_error", render: (value: string) => value || "-" },
  ];
  const autoRunEvidenceColumns: ColumnsType<TaskAutoRunEvidenceRow> = [
    { title: "账号", dataIndex: "account_name", key: "account_name", width: 180, render: (value: string, record) => (
      <Space direction="vertical" size={0}>
        <Typography.Text>{value || record.account_user_id}</Typography.Text>
        <Typography.Text type="secondary">{record.account_user_id}</Typography.Text>
      </Space>
    ) },
    { title: "题目", dataIndex: "item_id", key: "item_id", width: 160, render: (value: string) => value || "-" },
    { title: "提交", dataIndex: "success", key: "success", width: 96, render: (value: boolean | undefined, record) => (
      <Space wrap>
        <Tag color={!record.attempted ? "default" : value ? "green" : "red"}>{!record.attempted ? "未提交" : value ? "成功" : "失败"}</Tag>
        {record.submits_remote ? <Tag color="blue">已写远端</Tag> : null}
      </Space>
    ) },
    { title: "回读", dataIndex: "readback_ok", key: "readback_ok", width: 96, render: (value: boolean | undefined) => value === undefined ? "-" : <Tag color={value ? "green" : "red"}>{value ? "确认" : "异常"}</Tag> },
    { title: "提交时间", dataIndex: "submitted_at", key: "submitted_at", width: 180, render: (value: string | undefined) => value ? new Date(value).toLocaleString() : "-" },
    { title: "错误", dataIndex: "error", key: "error", ellipsis: true, render: (value: string | undefined, record) => value || record.message || "-" },
    { title: "校验返回", dataIndex: "verify_result", key: "verify_result", ellipsis: true, render: renderJsonValue },
    { title: "提交返回", dataIndex: "submit_result", key: "submit_result", ellipsis: true, render: renderJsonValue },
    { title: "回读返回", dataIndex: "readback_result", key: "readback_result", ellipsis: true, render: renderJsonValue },
  ];
  const autoRunPreflightColumns: ColumnsType<TaskAutoRunPreflightCheck> = [
    { title: "检查项", dataIndex: "title", key: "title" },
    { title: "状态", dataIndex: "status", key: "status", width: 96, render: (value: string) => <Tag color={value === "passed" ? "green" : "red"}>{value === "passed" ? "通过" : "阻塞"}</Tag> },
    { title: "说明", dataIndex: "detail", key: "detail" },
    { title: "下一步", dataIndex: "next_step", key: "next_step", render: (value: string) => value || "-" },
  ];
  let taskWorkbenchNextStep = "先读取当前题，确认题面和材料后再生成 AI 草稿。";
  if (detailAbilityDraft?.capability_enabled) {
    taskWorkbenchNextStep = "题型库已标记有做题能力；正式提交仍需高风险确认和回读验证。";
  } else if (detailAbilityDraft?.flow_stage === "real_no_submit_review") {
    taskWorkbenchNextStep = "真实题不提交结果待人工审核；审核通过后才启用做题能力。";
  } else if (detailAbilityDraft?.flow_stage === "real_no_submit_ready") {
    taskWorkbenchNextStep = "草稿已确认，请到 AI 标注能力工作台执行真实题不提交。";
  } else if (detailAbilityDraft) {
    taskWorkbenchNextStep = "能力草稿待审核，请先到 AI 标注能力工作台查看草稿并确认。";
  } else if (!capability) {
    taskWorkbenchNextStep = "先去 AI 标注能力工作台制作能力；录制能力只作为字段学习来源。";
  } else if (draftResult?.ok) {
    taskWorkbenchNextStep = "已有草稿结果，先核对最近结果；需要写入时再进入高级调试确认闸门。";
  } else if (questionContext?.ok) {
    taskWorkbenchNextStep = "已读取当前题面，可以生成 AI 草稿。";
  }
  const taskWorkbenchRecentResult = draftResult?.message ?? mediaInspectionProvider?.message ?? questionContext?.message ?? "暂无最近执行结果";

  return (
    <div className="page-stack">
      <Space align="center" style={{ justifyContent: "space-between", width: "100%" }}>
        <div>
          <Typography.Title level={2} style={{ marginBottom: 4 }}>任务生产工作台</Typography.Title>
          <Typography.Text type="secondary">驾驶舱看汇总，这里按任务队列下钻到账号分布、剩余题量、做题能力和生产控制。</Typography.Text>
        </div>
        <Space>
          <Button loading={loading} onClick={() => void loadTaskWorkbench()}>刷新列表</Button>
          <Button type="primary" loading={loading} onClick={() => void handleRefresh()}>刷新生产数据</Button>
        </Space>
      </Space>

      <Card title="任务队列总表">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space wrap size="large" align="center">
            <Tag color="blue">任务队列 {taskQueues.length}</Tag>
            <Tag color="green">有待处理 {taskQueues.filter((item) => item.pending > 0).length}</Tag>
            <Tag color="gold">待处理合计 {taskQueues.reduce((total, item) => total + item.pending, 0)}</Tag>
            <Tag color="cyan">生产账号 {productionDashboard?.account_count ?? 0}</Tag>
            {hiddenDuplicateCount ? <Tag color="purple">已合并重复 {hiddenDuplicateCount}</Tag> : null}
            <Typography.Text type="secondary">当前来源账号：{catalog?.source_account_user_id ?? "加载中"}</Typography.Text>
          </Space>
          {lastRefresh?.failed_count ? <Alert type="warning" showIcon message="刷新未完全成功" description={lastRefresh.message} /> : null}
          <Alert
            type="info"
            showIcon
            message="任务池口径"
            description="总表待处理按同一任务在各账号视图中的代表值展示，不把多个账号看到的同一题池重复相加；账号分布表保留每个账号看到的数，用来判断刷新时间差和账号权限差异。"
          />
          <Table
            className="task-queue-table"
            size="small"
            columns={taskQueueColumns}
            dataSource={taskQueues}
            rowKey="key"
            loading={loading}
            scroll={{ x: "max-content" }}
            pagination={{ pageSize: 10 }}
            expandable={{
              expandedRowKeys: expandedTaskQueueKeys,
              onExpandedRowsChange: (keys) => setExpandedTaskQueueKeys(keys.map(String)),
              expandedRowRender: (record) => (
                <Table<TaskQueueAccountRow>
                  className="task-queue-account-detail-table"
                  size="small"
                  columns={taskQueueAccountDetailColumns}
                  dataSource={record.accounts}
                  rowKey={(row) => row.account.user_id}
                  pagination={false}
                  scroll={{ x: "max-content" }}
                />
              ),
              rowExpandable: (record) => record.accounts.length > 0,
              showExpandColumn: false,
            }}
            rowClassName={(record) => record.key === selectedTaskQueue?.key ? "selected-task-row" : ""}
            onRow={(record) => ({ onClick: () => setSelectedTaskQueueKey(record.key) })}
          />
          <Descriptions bordered size="small" column={3}>
            <Descriptions.Item label="刷新方式">{lastRefresh ? "统一刷新生产数据" : "尚未刷新"}</Descriptions.Item>
            <Descriptions.Item label="刷新结果">{lastRefresh ? `成功 ${lastRefresh.refreshed_count} 个，失败 ${lastRefresh.failed_count} 个` : "未请求"}</Descriptions.Item>
            <Descriptions.Item label="状态文件">{lastRefresh?.state_path ?? "-"}</Descriptions.Item>
            <Descriptions.Item label="完成时间">{lastRefresh?.finished_at ? new Date(lastRefresh.finished_at).toLocaleString() : "-"}</Descriptions.Item>
            <Descriptions.Item label="错误提示">{lastRefresh?.failed_count ? lastRefresh.message : "无"}</Descriptions.Item>
          </Descriptions>
        </Space>
      </Card>

      <Card title="账号分布" extra={selectedTaskQueue ? <Tag color="blue">{selectedTaskQueue.task_name}</Tag> : null}>
        <Table
          columns={accountDistributionColumns}
          dataSource={selectedTaskQueue?.accounts ?? []}
          rowKey={(record) => `${record.account.user_id}-${record.task.task_id}`}
          pagination={{ pageSize: 8 }}
        />
      </Card>

      <Card title="生产控制区">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Alert
            type={queueExecutableReady(selectedTaskQueue) ? "success" : "warning"}
            showIcon
            message={queueExecutableReady(selectedTaskQueue) ? "该任务已进入有做题能力" : selectedTaskAbilityDraft ? abilityFlowLabel(selectedTaskAbilityDraft, Boolean(selectedTaskQueue?.catalog_item?.capability_available)).text : "该任务还未进入 AI 标注能力工作台流程"}
            description={queueExecutableReady(selectedTaskQueue) ? "可进入生产控制查看执行材料；正式提交仍需高风险确认。" : selectedTaskAbilityDraft ? "请到 AI 标注能力工作台继续下一步：草稿确认、真实题不提交或审核通过。" : "先去 AI 标注能力工作台制作能力，生成草稿并完成真实题不提交审核。"}
          />
          <Descriptions bordered size="small" column={3}>
            <Descriptions.Item label="选中任务">{selectedTaskQueue?.task_name ?? "-"}</Descriptions.Item>
            <Descriptions.Item label="待处理">{selectedTaskQueue?.pending ?? 0}</Descriptions.Item>
            <Descriptions.Item label="处理中">{selectedTaskQueue?.processing ?? 0}</Descriptions.Item>
            <Descriptions.Item label="进行中">{selectedTaskQueue?.in_progress ?? 0}</Descriptions.Item>
            <Descriptions.Item label="已废弃">{selectedTaskQueue?.abandoned ?? 0}</Descriptions.Item>
            <Descriptions.Item label="覆盖账号">{selectedTaskQueue?.account_count ?? 0}</Descriptions.Item>
            <Descriptions.Item label="任务ID">{selectedTaskQueue?.task_id || "-"}</Descriptions.Item>
            <Descriptions.Item label="题型流程">{selectedTaskAbilityDraft ? `${selectedTaskAbilityDraft.status} / ${selectedTaskAbilityDraft.version}` : "未提交规则材料"}</Descriptions.Item>
            <Descriptions.Item label="录制能力">{selectedTaskQueue?.catalog_item?.capability_available ? `有录制 ${selectedTaskQueue.catalog_item.capability_recording_count} 份` : "无录制能力"}</Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Button type="primary" disabled={!canOpenTaskOperation(selectedTaskQueue)} onClick={() => void openTaskCapabilityDetail()}>打开任务操作台</Button>
            <Button disabled={!selectedTaskQueue?.accounts.length} onClick={() => selectedTaskQueue?.accounts[0] && openAccountTaskPage(selectedTaskQueue.accounts[0].account)}>打开任务页</Button>
            <Button href="/ai">AI 配置/模型健康</Button>
            <Button href={`/ability-workbench${selectedTaskQueue?.task_id ? `?task_id=${selectedTaskQueue.task_id}` : ""}`}>去 AI 标注能力工作台制作</Button>
          </Space>
        </Space>
      </Card>

      <Card title="真实任务目录明细" extra={<Space><Switch checked={showOnlyPendingTasks} onChange={setShowOnlyPendingTasks} /><Typography.Text>只看有待处理任务</Typography.Text></Space>}>
        <Table columns={columns} dataSource={visibleTasks} rowKey={(record) => `${record.source_account_user_id}-${record.task_id}-${record.id}`} loading={loading} pagination={{ pageSize: 8 }} />
      </Card>

      <Drawer title="任务操作台" width={560} open={drawerOpen} onClose={() => setDrawerOpen(false)}>
        {detail ? (
          <div className="page-stack">
            <Card title="任务操作台" size="small">
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Alert
                  type={autoAbilityReady ? "success" : "warning"}
                  showIcon
                  message={detailAbilityLabel.text}
                  description={taskWorkbenchNextStep}
                />
                <Descriptions bordered column={2} size="small">
                  <Descriptions.Item label="任务">{detail.item.task_short_name || detail.item.raw_task_name || detail.item.task_name_id}</Descriptions.Item>
                  <Descriptions.Item label="状态"><Tag color={statusColorMap[detail.item.task_status_color]}>{detail.item.task_status_raw}</Tag></Descriptions.Item>
                  <Descriptions.Item label="待处理">{detail.item.last_task_page_error ? "未验证" : (detail.item.pending_raw || "0")}</Descriptions.Item>
                  <Descriptions.Item label="题型能力">{detailAbilityDraft ? `${detailAbilityDraft.status} / ${detailAbilityDraft.version}` : "未提交规则材料"}</Descriptions.Item>
                  <Descriptions.Item label="录制能力" span={2}>{capability ? capability.capability_level : "待学习"}</Descriptions.Item>
                  <Descriptions.Item label="推荐下一步" span={2}>{taskWorkbenchNextStep}</Descriptions.Item>
                  <Descriptions.Item label="最近结果" span={2}>{taskWorkbenchRecentResult}</Descriptions.Item>
                </Descriptions>
                {drawerCanUseAnswerCapability ? (
                  <>
                <Card size="small" title="生产资格">
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    <Descriptions bordered size="small" column={2}>
                      <Descriptions.Item label="自动生产">{productionStatus?.production_allowed ? "已允许" : "未允许"}</Descriptions.Item>
                      <Descriptions.Item label="能力版本">{productionStatus?.ability_version || detailAbilityDraft?.version || "-"}</Descriptions.Item>
                      <Descriptions.Item label="Prompt 版本">{productionStatus?.prompt_version || "-"}</Descriptions.Item>
                      <Descriptions.Item label="可用设备">{productionStatus?.available_device_count ?? productionDevices.length}</Descriptions.Item>
                      <Descriptions.Item label="当前状态">{autoRun?.status || "未启动"}</Descriptions.Item>
                      <Descriptions.Item label="已处理">{autoRun?.selected_account_count ?? 0}</Descriptions.Item>
                      <Descriptions.Item label="成功">{autoRun?.healthy_account_count ?? 0}</Descriptions.Item>
                      <Descriptions.Item label="失败">{autoRun?.abnormal_account_count ?? 0}</Descriptions.Item>
                    </Descriptions>
                    {productionStatus?.missing_requirements?.length ? <Alert type="warning" showIcon message="未允许启动生产" description={productionStatus.missing_requirements.join("；")} /> : null}
                  </Space>
                </Card>
                <Card size="small" title="生产配置">
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Typography.Text strong>做题账号</Typography.Text>
                      <Select
                        style={{ width: "100%" }}
                        value={productionForm.account_scope.mode}
                        onChange={(mode: StartProductionPayload["account_scope"]["mode"]) => updateProductionForm({ account_scope: { ...productionForm.account_scope, mode } })}
                        options={[{ value: "all_available", label: "全部可用账号" }, { value: "specified", label: "指定账号" }]}
                      />
                      {productionForm.account_scope.mode === "specified" ? (
                        <Select
                          mode="multiple"
                          allowClear
                          style={{ width: "100%" }}
                          placeholder="选择做题账号"
                          value={productionForm.account_scope.account_user_ids}
                          onChange={(account_user_ids) => updateProductionForm({ account_scope: { mode: "specified", account_user_ids } })}
                          options={taskAccountRows.map((row) => ({ value: row.account.user_id, label: `${row.account.custom_name || row.account.display_name || row.account.user_id}｜待处理 ${row.task.pending}｜返修 ${row.task.repair}`, disabled: !row.account.cookie_synced || row.account.status === "disabled" }))}
                        />
                      ) : null}
                    </Space>
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Typography.Text strong>题目范围</Typography.Text>
                      <Select
                        style={{ width: "100%" }}
                        value={productionForm.question_scope.mode}
                        onChange={(mode: StartProductionPayload["question_scope"]["mode"]) => updateProductionForm({ question_scope: { mode } })}
                        options={[{ value: "pending", label: "待处理" }, { value: "repair", label: "返修" }, { value: "pending_repair", label: "待处理+返修" }]}
                      />
                    </Space>
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Typography.Text strong>执行方式</Typography.Text>
                      <Select
                        style={{ width: "100%" }}
                        value={productionForm.execution_mode}
                        onChange={(execution_mode: StartProductionPayload["execution_mode"]) => updateProductionForm({ execution_mode })}
                        options={[{ value: "platform", label: "平台" }, { value: "platform_plus_devices", label: "平台+设备" }, { value: "devices", label: "设备" }]}
                      />
                    </Space>
                    {productionForm.execution_mode !== "platform" ? (
                      <Space direction="vertical" style={{ width: "100%" }}>
                        <Typography.Text strong>执行设备</Typography.Text>
                        <Select
                          style={{ width: "100%" }}
                          value={productionForm.device_scope.mode}
                          onChange={(mode: StartProductionPayload["device_scope"]["mode"]) => updateProductionForm({ device_scope: { ...productionForm.device_scope, mode } })}
                          options={[{ value: "auto", label: "自动分配设备" }, { value: "specified", label: "指定设备" }]}
                        />
                        {productionForm.device_scope.mode === "specified" ? (
                          <Select
                            mode="multiple"
                            allowClear
                            style={{ width: "100%" }}
                            placeholder="选择执行设备"
                            value={productionForm.device_scope.worker_ids}
                            onChange={(worker_ids) => updateProductionForm({ device_scope: { mode: "specified", worker_ids } })}
                            options={productionDevices.map((device) => ({ value: device.worker_id, label: `${device.device_name || device.worker_id}｜${device.running_slots}/${device.manual_slots}｜${device.current_state}`, disabled: !device.usable_for_production }))}
                          />
                        ) : null}
                      </Space>
                    ) : null}
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Typography.Text strong>本次最多处理</Typography.Text>
                      <Select
                        style={{ width: "100%" }}
                        value={productionMaxMode}
                        onChange={setProductionMaxMode}
                        options={[{ value: "limited", label: "指定数量" }]}
                      />
                      {productionMaxMode === "limited" ? (
                        <InputNumber min={1} style={{ width: "100%" }} value={productionForm.limits.max_items_total ?? 1} onChange={(value) => updateProductionForm({ limits: { ...productionForm.limits, max_items_total: Number(value ?? 1) } })} />
                      ) : null}
                    </Space>
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Typography.Text strong>连续失败</Typography.Text>
                      <Space.Compact style={{ width: "100%" }}>
                        <InputNumber min={1} style={{ width: "100%" }} value={productionForm.limits.failure_threshold} onChange={(value) => updateProductionForm({ limits: { ...productionForm.limits, failure_threshold: Number(value ?? 3) } })} />
                        <Button disabled>次后暂停</Button>
                      </Space.Compact>
                    </Space>
                    <Descriptions bordered size="small" column={1}>
                      <Descriptions.Item label="生产配置摘要">{productionForm.execution_mode} / {productionForm.question_scope.mode} / {productionForm.limits.max_items_total}</Descriptions.Item>
                      <Descriptions.Item label="当前执行设备/账号">{autoRun?.accounts.map((item) => item.account_user_id).join("、") || "未启动"}</Descriptions.Item>
                      <Descriptions.Item label="最近错误">{autoRun?.last_error || "无"}</Descriptions.Item>
                    </Descriptions>
                    <Space wrap>
                      <Button type="primary" href={`/ability-workbench${detail?.item.task_id ? `?task_id=${detail.item.task_id}` : ""}`}>去能力工作台启动生产</Button>
                      <Button disabled={!autoRun?.run_id || autoRun.status === "paused"} loading={productionLoading} onClick={() => void pauseProductionRun()}>暂停生产</Button>
                      <Button disabled={!autoRun?.run_id || autoRun.status !== "paused"} loading={productionLoading} onClick={() => void resumeProductionRun()}>恢复生产</Button>
                      <Button danger disabled={!autoRun?.run_id || autoRun.status === "stopped"} loading={productionLoading} onClick={() => void stopProductionRun()}>停止生产</Button>
                      <Button disabled={!autoRun?.run_id} onClick={() => autoRun?.run_id && void refreshAutoRun(autoRun.run_id)}>查看运行记录</Button>
                      <Button disabled={!autoRun?.run_id} onClick={() => message.info("查看日志请到执行设备管理页的高级调试。")}>查看日志</Button>
                      <Button autoInsertSpace={false} onClick={() => setDrawerOpen(false)}>取消</Button>
                    </Space>
                  </Space>
                </Card>
                <Card size="small" title="端到端做题不提交">
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    <Alert
                      type="info"
                      showIcon
                      message="统一预览入口"
                      description={isBon8Task ? "bon8 旧任务页不再执行真实题预览、首题审核或正式提交；请统一到能力工作台 Step3/Step4 处理。" : "科研图在这里直接执行真实题不提交，把 AI 答案保存到做题界面供你核对；不正式提交。"}
                    />
                    <Descriptions bordered size="small" column={2}>
                      <Descriptions.Item label="预览账号">{previewAccountLabel}</Descriptions.Item>
                      <Descriptions.Item label="当前范围">{isBon8Task ? "已领取处理中/返修题" : "真实题不提交"}</Descriptions.Item>
                      <Descriptions.Item label="正式提交">{noSubmitResult?.submits_remote ? "是" : "否"}</Descriptions.Item>
                      <Descriptions.Item label="写入做题界面">{noSubmitResult ? (noSubmitResult.saved_to_task_ui ? "已保存" : "未保存") : "-"}</Descriptions.Item>
                    </Descriptions>
                    <Select
                      style={{ width: "100%" }}
                      value={previewAccountUserId || undefined}
                      onChange={setPreviewAccountUserId}
                      placeholder="选择一个用于端到端不提交预览的账号"
                      options={taskAccountRows.map((row) => ({
                        value: row.account.user_id,
                        label: `${row.account.custom_name || row.account.display_name || row.account.user_id}${(isBon8Task ? taskHasRunnableAutoQuestion(row) : taskHasCurrentQuestion(row)) ? "" : `（${taskAutoReceiveReason(row)}）`}`,
                        disabled: !row.account.cookie_synced || row.account.status === "disabled" || (isBon8Task ? !taskHasRunnableAutoQuestion(row) : !taskHasCurrentQuestion(row)),
                      }))}
                    />
                    <Space wrap>
                      {isBon8Task ? (
                        <Button type="primary" href={`/ability-workbench${detail.item.task_id ? `?task_id=${detail.item.task_id}` : ""}`}>
                          进入能力工作台
                        </Button>
                      ) : (
                        <Button type="primary" loading={noSubmitLoading} disabled={!previewAccountUserId} onClick={() => void handleTaskEndToEndNoSubmit()}>
                          {noSubmitResult ? "重新执行端到端做题不提交" : "端到端做题不提交"}
                        </Button>
                      )}
                      <Typography.Text type="secondary">
                        {isBon8Task ? "旧任务页只保留状态查看；调教、审核和生产启动统一走能力工作台。" : "先看 AI 写入结果是否合理，再决定是否继续调整提示词或启用能力。"}
                      </Typography.Text>
                    </Space>
                    {noSubmitResult ? (
                      <Descriptions bordered size="small" column={2}>
                        <Descriptions.Item label="阶段">{noSubmitResult.stage || "-"}</Descriptions.Item>
                        <Descriptions.Item label="审核状态">{noSubmitResult.review_status || "-"}</Descriptions.Item>
                        <Descriptions.Item label="执行账号">{noSubmitResult.account_user_id || "-"}</Descriptions.Item>
                        <Descriptions.Item label="题目ID">{noSubmitResult.item_id || "-"}</Descriptions.Item>
                        <Descriptions.Item label="触网/写远端">{noSubmitResult.sends_network ? "是" : "否"} / {noSubmitResult.writes_remote ? "是" : "否"}</Descriptions.Item>
                        <Descriptions.Item label="证据文件">{noSubmitResult.review_artifact_path || "-"}</Descriptions.Item>
                        <Descriptions.Item label="AI 结果" span={2}><pre className="pre-wrap">{JSON.stringify(noSubmitResult.answer_preview, null, 2)}</pre></Descriptions.Item>
                        <Descriptions.Item label="暂存结果" span={2}><pre className="pre-wrap">{JSON.stringify(noSubmitResult.temp_result, null, 2)}</pre></Descriptions.Item>
                        {Object.keys(noSubmitResult.verify_result).length ? (
                          <Descriptions.Item label="提交前校验" span={2}><pre className="pre-wrap">{JSON.stringify(noSubmitResult.verify_result, null, 2)}</pre></Descriptions.Item>
                        ) : null}
                        <Descriptions.Item label="结果说明" span={2}>{noSubmitResult.ui_review_hint || noSubmitResult.message}</Descriptions.Item>
                      </Descriptions>
                    ) : null}
                  </Space>
                </Card>
                <Card size="small" title="AI 自动做题">
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    <Alert
                      type={autoAbilityReady ? "success" : "warning"}
                      showIcon
                      message={autoAbilityReady ? (isBon8Task ? "该任务已接入 bon8 通用自动做题" : "该任务已发布 AI 做题能力") : "该任务还没有可执行能力"}
                      description={autoAbilityReady ? (isBon8Task ? "当前复用生产控制执行 bon8 已领取的处理中/返修题；pending-only 账号不在本轮范围内。" : "默认启动当前任务所有可用、当前有题且满足自动循环资格的账号；已有处理中题的账号可直接继续做题，pending-only 账号则仍需平台允许自动领题。") : "先去 AI 标注能力工作台制作能力、验证并发布后，才允许自动做题。"}
                    />
                    {!autoAbilityReady ? (
                      <Button type="primary" href={`/ability-workbench${detail?.item.task_id ? `?task_id=${detail.item.task_id}` : ""}`}>去 AI 标注能力工作台制作</Button>
                    ) : (
                      <>
                        {autoRunWaitingForItems ? (
                          <Alert
                            type="warning"
                            showIcon
                            message={blockedAutoReceiveRows.length ? "当前无可进入自动循环的账号" : "当前无可执行题"}
                            description={blockedAutoReceiveRows.length ? blockedAutoReceiveSummary : "该任务当前没有待处理、处理中或返修题；自动做题保持等待有题状态，已禁止启动正式提交。可以先运行启动前自检，检查能力、账号 Cookie 和证据目录。"}
                          />
                        ) : null}
                        <Descriptions bordered size="small" column={3}>
                          <Descriptions.Item label="执行器">{isBon8Task ? "bon8 已接入" : isResearchChartTask ? "科研图能力已挂载" : "待接入"}</Descriptions.Item>
                          <Descriptions.Item label="自动提交确认">能力发布后确认一次</Descriptions.Item>
                          <Descriptions.Item label="执行端">平台本机优先</Descriptions.Item>
                          <Descriptions.Item label="并发">按题型配置</Descriptions.Item>
                          <Descriptions.Item label="停止语义">立即停止</Descriptions.Item>
                          <Descriptions.Item label="运行状态"><Tag color={autoRunHealthy ? "green" : "red"}>{autoRunHealthy ? "正常" : "异常"}</Tag></Descriptions.Item>
                        </Descriptions>
                        <Space wrap align="center">
                          <Typography.Text strong>执行账号</Typography.Text>
                          <Tag color="blue">只展示有此任务的账号</Tag>
                          <Tag color="orange">仅满足自动循环资格的账号可入循环</Tag>
                          <Button size="small" disabled={!runnableTaskAccountIds.length} onClick={() => setSelectedAutoAccountIds(runnableTaskAccountIds)}>选择当前有题账号</Button>
                          <Button size="small" onClick={() => setSelectedAutoAccountIds([])}>清空</Button>
                        </Space>
                        <Select
                          mode="multiple"
                          allowClear
                          style={{ width: "100%" }}
                          placeholder="默认选择当前有题、Cookie 正常且满足自动循环资格的账号"
                          value={selectedAutoAccountIds}
                          onChange={setSelectedAutoAccountIds}
                          options={taskAccountRows.map((row) => ({
                            value: row.account.user_id,
                            label: `${row.account.custom_name || row.account.display_name || row.account.user_id}${taskCanAutoReceive(row) ? "" : `（${taskAutoReceiveReason(row)}）`}`,
                            disabled: !row.account.cookie_synced || row.account.status === "disabled" || !taskHasCurrentQuestion(row) || !taskCanAutoReceive(row),
                          }))}
                        />
                        <Table
                          size="small"
                          columns={autoAccountColumns}
                          dataSource={taskAccountRows}
                          rowKey={(record) => record.account.user_id}
                          pagination={false}
                        />
                        <Space wrap>
                          <Button loading={autoRunLoading} onClick={() => void runAutoTaskPreflight()}>启动前自检</Button>
                          <Button type="primary" href={`/ability-workbench${selectedTaskQueue?.task_id ? `?task_id=${selectedTaskQueue.task_id}` : ""}`}>进入能力工作台</Button>
                          {autoRun?.run_id ? <Button loading={autoRunLoading} onClick={() => void refreshAutoRun()}>刷新运行状态</Button> : null}
                          {autoRun?.run_id && !autoRunWorkerStatus?.active ? <Button loading={autoRunLoading} onClick={() => void startAutoTaskWorker()}>启动后台循环</Button> : null}
                          {autoRun?.run_id ? <Button danger loading={autoRunLoading} onClick={() => void stopAutoTaskRun()}>立即停止</Button> : null}
                        </Space>
                        {autoRunPreflight ? (
                          <Collapse
                            defaultActiveKey={["preflight"]}
                            items={[
                              {
                                key: "preflight",
                                label: "启动前自检",
                                children: (
                                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                    <Alert
                                      type={autoRunPreflight.can_start ? "success" : "warning"}
                                      showIcon
                                      message={autoRunPreflight.message}
                                      description={autoRunPreflight.next_step}
                                    />
                                    <Table
                                      size="small"
                                      columns={autoRunPreflightColumns}
                                      dataSource={autoRunPreflight.checks}
                                      rowKey={(record) => record.key}
                                      pagination={false}
                                    />
                                  </Space>
                                ),
                              },
                            ]}
                          />
                        ) : null}
                        {autoRun?.accounts.length ? (
                          <Table
                            size="small"
                            columns={autoRunAccountColumns}
                            dataSource={autoRun.accounts}
                            rowKey={(record) => record.account_user_id}
                            pagination={false}
                          />
                        ) : null}
                        {autoRun ? (
                          <Collapse
                            defaultActiveKey={[]}
                            items={[
                              {
                                key: "submit-evidence",
                                label: "提交证据",
                                children: (
                                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                    <Alert
                                      type={autoRunEvidenceRows.length ? "info" : "warning"}
                                      showIcon
                                      message={autoRunEvidenceRows.length ? "正式提交证据已从运行状态读取" : "暂无正式提交证据"}
                                      description={autoRunEvidenceRows.length ? "这里展示 verify/submit、SubmitItem 和提交后回读结果；首屏仍只展示运行状态是否正常。" : "启动后台循环并完成一次提交闸门后，这里会显示账号级证据。"}
                                    />
                                    <Table
                                      size="small"
                                      columns={autoRunEvidenceColumns}
                                      dataSource={autoRunEvidenceRows}
                                      rowKey={(record) => `${record.account_user_id}-${record.item_id || "no-item"}`}
                                      pagination={false}
                                      scroll={{ x: "max-content" }}
                                    />
                                  </Space>
                                ),
                              },
                            ]}
                          />
                        ) : null}
                      </>
                    )}
                  </Space>
                </Card>
                <Space wrap>
                  <Button disabled={!selectedTaskQueue?.accounts.length} onClick={() => selectedTaskQueue?.accounts[0] && openAccountTaskPage(selectedTaskQueue.accounts[0].account)}>打开任务页</Button>
                  <Button loading={questionContextLoading} disabled={!capability} onClick={() => void handleLiveQuestionContextFetch()}>读取当前题</Button>
                  <Button type="primary" loading={draftLoading} disabled={!capability} onClick={() => void handleProviderDraftDryRun(true)}>生成 AI 草稿</Button>
                  <Button href="/ability-workbench">进入能力工作台</Button>
                </Space>
                <Typography.Text type="secondary">默认操作不会提交答案；写远端、暂存和提交前校验仍在高级调试里受闸门控制。</Typography.Text>
                  </>
                ) : (
                  <Card size="small" title="只读 operation 流程计划">
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <Alert
                        type="info"
                        showIcon
                        message="该任务还没有可执行做题能力"
                        description="当前操作台只展示任务处理流程，不提供生产参数、端到端写入或自动做题入口。请先去 AI 标注能力工作台制作能力。"
                      />
                      {operationProcessPlan ? (
                        <>
                          <Descriptions bordered column={1} size="small">
                            <Descriptions.Item label="入口">
                              <Typography.Link href={operationProcessPlan.operation_url} target="_blank" rel="noreferrer">
                                {operationProcessPlan.operation_url}
                              </Typography.Link>
                            </Descriptions.Item>
                            <Descriptions.Item label="来源账号">{operationProcessPlan.source_account_user_id}</Descriptions.Item>
                            <Descriptions.Item label="任务">{operationProcessPlan.task_type_name} / {operationProcessPlan.task_id}</Descriptions.Item>
                            <Descriptions.Item label="领题/触网/写远端/提交">
                              {operationProcessPlan.claims_task ? "是" : "否"} / {operationProcessPlan.sends_network ? "是" : "否"} / {operationProcessPlan.writes_remote ? "是" : "否"} / {operationProcessPlan.submits_answer ? "是" : "否"}
                            </Descriptions.Item>
                            <Descriptions.Item label="处理后读题">{operationProcessPlan.post_claim_read_step}</Descriptions.Item>
                            <Descriptions.Item label="答案写入">{operationProcessPlan.answer_write_step}</Descriptions.Item>
                            <Descriptions.Item label="护栏">{operationProcessPlan.guardrails.join("；")}</Descriptions.Item>
                          </Descriptions>
                          <Table
                            size="small"
                            columns={decisionPipelineColumns}
                            dataSource={operationProcessPlan.steps}
                            rowKey={(record) => record.key}
                            pagination={false}
                          />
                        </>
                      ) : (
                        <Alert type="warning" showIcon message="暂未读取到 operation 流程计划" description="请刷新任务数据后重试，或直接进入 AI 标注能力工作台提交规则材料。" />
                      )}
                      <Space wrap>
                        <Button type="primary" href={`/ability-workbench${detail.item.task_id ? `?task_id=${detail.item.task_id}` : ""}`}>去 AI 标注能力工作台制作</Button>
                        <Button disabled={!selectedTaskQueue?.accounts.length} onClick={() => selectedTaskQueue?.accounts[0] && openAccountTaskPage(selectedTaskQueue.accounts[0].account)}>打开任务页</Button>
                      </Space>
                    </Space>
                  </Card>
                )}
              </Space>
            </Card>
            <Collapse
              defaultActiveKey={[]}
              items={[
                {
                  key: "advanced",
                  label: "高级调试",
                  children: (
                    <div className="page-stack">
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="任务名称ID">{detail.item.task_name_id}</Descriptions.Item>
              <Descriptions.Item label="来源账号">{detail.source_account_user_id}</Descriptions.Item>
              <Descriptions.Item label="覆盖账号数">{detail.covered_account_count}</Descriptions.Item>
              <Descriptions.Item label="任务状态"><Tag color={statusColorMap[detail.item.task_status_color]}>{detail.item.task_status_raw}</Tag></Descriptions.Item>
              <Descriptions.Item label="待处理">{detail.item.last_task_page_error ? "未验证（旧缓存已隐藏）" : (detail.item.pending_raw || "0")}</Descriptions.Item>
              <Descriptions.Item label="最近失败">{detail.latest_failure || "无"}</Descriptions.Item>
            </Descriptions>
            {isBon8Task && bon8AdvancedRun?.confirmation_sheet ? (
              <Card title="bon8 首题审核（高级区）" size="small">
                <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                  <Alert
                    type="info"
                    showIcon
                    message="bon8 专属首题审核已改为只读状态"
                    description="旧任务页不再提供批准首题或正式提交入口；如需继续调教、审核或启动生产，请统一进入能力工作台。"
                  />
                  <Descriptions bordered size="small" column={2}>
                    <Descriptions.Item label="run 状态">{bon8AdvancedRun.status}</Descriptions.Item>
                    <Descriptions.Item label="闸门状态">{bon8AdvancedRun.gate_status}</Descriptions.Item>
                    <Descriptions.Item label="确认单状态">{bon8AdvancedRun.confirmation_sheet.status}</Descriptions.Item>
                    <Descriptions.Item label="首题账号">{bon8AdvancedRun.confirmation_sheet.account_user_id || "-"}</Descriptions.Item>
                    <Descriptions.Item label="题目ID" span={2}>{bon8AdvancedRun.confirmation_sheet.item_id || "-"}</Descriptions.Item>
                    <Descriptions.Item label="审核件路径" span={2}>{bon8AdvancedRun.confirmation_sheet.evidence_path || bon8AdvancedRun.confirmation_sheet.review_payload_path || "-"}</Descriptions.Item>
                    <Descriptions.Item label="下一步" span={2}>{bon8AdvancedRun.next_step}</Descriptions.Item>
                  </Descriptions>
                  <Space wrap>
                    <Button type="primary" href={`/ability-workbench${detail.item.task_id ? `?task_id=${detail.item.task_id}` : ""}`}>
                      去能力工作台继续审核
                    </Button>
                  </Space>
                </Space>
              </Card>
            ) : null}
            <Card title="事件时间线" size="small">
              <Timeline items={detail.timeline.map((event) => ({ children: `${new Date(event.created_at).toLocaleString()} · ${event.event_type} · ${event.message} · ${event.status_raw || "-"} · ${event.pending_raw || "-"}` }))} />
            </Card>
            <Card title="题型能力卡" size="small">
              {capability ? (
                <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                  <Descriptions bordered column={1} size="small">
                    <Descriptions.Item label="学习状态"><Tag color={capability.state === "http_draft_verified" ? "green" : "gold"}>{capability.state}</Tag></Descriptions.Item>
                    <Descriptions.Item label="能力层级">{capability.capability_level}</Descriptions.Item>
                    <Descriptions.Item label="草稿接口">{capability.endpoint}</Descriptions.Item>
                    <Descriptions.Item label="Payload">{`TaskID ${capability.identity.TaskID} / NodeID ${capability.identity.NodeID} / ItemID ${capability.identity.ItemID}`}</Descriptions.Item>
                    <Descriptions.Item label="录制证据">{capability.recording_count} 份</Descriptions.Item>
                    <Descriptions.Item label="验证">{`${capability.latest_validation.request_count} 次请求，成功响应 ${capability.latest_validation.success_response_count} 次`}</Descriptions.Item>
                  </Descriptions>
                  <Alert
                    type="warning"
                    showIcon
                    message="当前入口只处理草稿暂存"
                    description="默认 dry-run 不触网；真实暂存还需要后端环境变量闸门。页面不会提交、继续下一题、放弃或领取。"
                  />
                  <Space wrap>
                    {capability.ai_input_requirements.map((item) => <Tag key={item}>{item}</Tag>)}
                  </Space>
                  <Card title="AI 输入材料" size="small">
                    <Space direction="vertical" size="small" style={{ width: "100%" }}>
                      {(capability.ai_input_materials.length ? capability.ai_input_materials : capability.ai_input_requirements).map((item) => (
                        <Typography.Text key={item} copyable>{item}</Typography.Text>
                      ))}
                    </Space>
                  </Card>
                  <Card title="AI 输入材料规范" size="small">
                    <Table
                      size="small"
                      columns={inputSpecColumns}
                      dataSource={capability.ai_input_spec}
                      rowKey={(record) => record.key}
                      pagination={false}
                    />
                  </Card>
                  {operationProcessPlan ? (
                    <Card title="operation 处理领题入口" size="small">
                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                        <Alert
                          type="warning"
                          showIcon
                          message={operationProcessPlan.message}
                          description="这是有副作用的领题步骤：点处理会占用/分配题目，但不会提交答案；处理后的当前 ItemID 再进入只读题面读取、AI 判题、草稿写入和最终提交。"
                        />
                        <Descriptions bordered column={1} size="small">
                          <Descriptions.Item label="入口">
                            <Typography.Link href={operationProcessPlan.operation_url} target="_blank" rel="noreferrer">
                              {operationProcessPlan.operation_url}
                            </Typography.Link>
                          </Descriptions.Item>
                          <Descriptions.Item label="来源账号">{operationProcessPlan.source_account_user_id}</Descriptions.Item>
                          <Descriptions.Item label="任务">{operationProcessPlan.task_type_name} / {operationProcessPlan.task_id}</Descriptions.Item>
                          <Descriptions.Item label="领题/触网/写远端/提交">
                            {operationProcessPlan.claims_task ? "是" : "否"} / {operationProcessPlan.sends_network ? "是" : "否"} / {operationProcessPlan.writes_remote ? "是" : "否"} / {operationProcessPlan.submits_answer ? "是" : "否"}
                          </Descriptions.Item>
                          <Descriptions.Item label="处理后读题">{operationProcessPlan.post_claim_read_step}</Descriptions.Item>
                          <Descriptions.Item label="答案写入">{operationProcessPlan.answer_write_step}</Descriptions.Item>
                          <Descriptions.Item label="护栏">{operationProcessPlan.guardrails.join("；")}</Descriptions.Item>
                        </Descriptions>
                        <Table
                          size="small"
                          columns={decisionPipelineColumns}
                          dataSource={operationProcessPlan.steps}
                          rowKey={(record) => record.key}
                          pagination={false}
                        />
                      </Space>
                    </Card>
                  ) : null}
                  {questionContext ? (
                    <Card title="不打开 AIDP UI 的题面上下文" size="small">
                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                        <Alert
                          type="info"
                          showIcon
                          message={questionContext.message}
                          description={`来源=${questionContext.source_mode}；触网=${questionContext.sends_network ? "是" : "否"}；写远端=${questionContext.writes_remote ? "是" : "否"}`}
                        />
                        <Space wrap>
                          <Button loading={questionContextLoading} onClick={() => void handleLiveQuestionContextFetch()}>
                            尝试 MGetAnswerList 只读取题
                          </Button>
                          <Typography.Text type="secondary">
                            只允许读取当前答案列表；不会调用 Receive/PreReceive/SubmitTempItemAnswer。
                          </Typography.Text>
                        </Space>
                        <Descriptions bordered column={1} size="small">
                          <Descriptions.Item label="Payload">{`TaskID ${questionContext.identity.TaskID} / NodeID ${questionContext.identity.NodeID} / ItemID ${questionContext.identity.ItemID}`}</Descriptions.Item>
                          <Descriptions.Item label="允许保存接口">{String(questionContext.payload_identity.allowed_save_endpoint ?? "-")}</Descriptions.Item>
                          <Descriptions.Item label="当前草稿字段">
                            <Typography.Text copyable>{JSON.stringify(questionContext.current_answer_data, null, 2)}</Typography.Text>
                          </Descriptions.Item>
                          <Descriptions.Item label="阻塞能力">{questionContext.blockers.length ? questionContext.blockers.join("；") : "无"}</Descriptions.Item>
                          <Descriptions.Item label="证据">{questionContext.evidence_path ?? "-"}</Descriptions.Item>
                        </Descriptions>
                        <Table
                          size="small"
                          columns={materialResourceColumns}
                          dataSource={questionContext.material_resources}
                          rowKey={(record) => record.key}
                          pagination={false}
                        />
                        <Table
                          size="small"
                          columns={decisionPipelineColumns}
                          dataSource={questionContext.decision_pipeline}
                          rowKey={(record) => record.key}
                          pagination={false}
                        />
                        <Table
                          size="small"
                          columns={iterationCandidateColumns}
                          dataSource={questionContext.iteration_candidates}
                          rowKey={(record) => record.key}
                          pagination={false}
                        />
                        <Card title="沙箱点击候选计划" size="small">
                          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                            <Alert
                              type="info"
                              showIcon
                              message="首版只提取候选，不执行点击"
                              description="把题目网页 HTML 快照粘贴进来后，平台会提取 link/button/role/onclick 等候选；后续再接独立浏览器真实点击。"
                            />
                            <Input.TextArea
                              rows={5}
                              value={sandboxHtmlSnapshot}
                              onChange={(event) => setSandboxHtmlSnapshot(event.target.value)}
                              placeholder="<button>...</button>"
                            />
                            <Space wrap>
                              <Button loading={questionContextLoading} onClick={() => void handleSandboxClickPlan()}>
                                生成沙箱点击候选
                              </Button>
                              <Typography.Text type="secondary">不触网、不点击、不写远端。</Typography.Text>
                            </Space>
                            {sandboxClickPlan ? (
                              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                <Descriptions bordered column={1} size="small">
                                  <Descriptions.Item label="结果">{sandboxClickPlan.ok ? "通过" : "阻塞"}</Descriptions.Item>
                                  <Descriptions.Item label="来源">{sandboxClickPlan.source_mode}</Descriptions.Item>
                                  <Descriptions.Item label="触网/写远端/执行点击">{sandboxClickPlan.sends_network ? "是" : "否"} / {sandboxClickPlan.writes_remote ? "是" : "否"} / {sandboxClickPlan.executes_clicks ? "是" : "否"}</Descriptions.Item>
                                  <Descriptions.Item label="网页 URL">{sandboxClickPlan.html_url || "-"}</Descriptions.Item>
                                  <Descriptions.Item label="阻塞">{sandboxClickPlan.blockers.length ? sandboxClickPlan.blockers.join("；") : "无"}</Descriptions.Item>
                                  <Descriptions.Item label="消息">{sandboxClickPlan.message}</Descriptions.Item>
                                </Descriptions>
                                <Table
                                  size="small"
                                  columns={sandboxCandidateColumns}
                                  dataSource={sandboxClickPlan.click_candidates}
                                  rowKey={(record) => `${record.selector}-${record.reason}`}
                                  pagination={false}
                                />
                                <Table
                                  size="small"
                                  columns={sandboxStepColumns}
                                  dataSource={sandboxClickPlan.next_steps}
                                  rowKey={(record) => record.key}
                                  pagination={false}
                                />
                                <Card title="独立沙箱点击执行" size="small">
                                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                    <Alert
                                      type="warning"
                                      showIcon
                                      message="会调用本机 helper 的 headless Edge 沙箱"
                                      description="只加载题目网页 URL，按低风险候选最多点击 3 个，记录跳转、交互、动效和弹窗信号；不打开 AIDP UI、不写远端。"
                                    />
                                    <Space wrap>
                                      <Button loading={questionContextLoading} onClick={() => void handleSandboxClickExecution()} disabled={!sandboxClickPlan.ok || sandboxClickPlan.click_candidates.length === 0}>
                                        执行独立沙箱点击
                                      </Button>
                                      <Typography.Text type="secondary">需要本机 helper 0.4.14+。</Typography.Text>
                                    </Space>
                                    {sandboxClickExecution ? (
                                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                        <Descriptions bordered column={1} size="small">
                                          <Descriptions.Item label="结果">{sandboxClickExecution.ok ? "通过" : "阻塞"}</Descriptions.Item>
                                          <Descriptions.Item label="触网/写远端/执行点击">{sandboxClickExecution.sends_network ? "是" : "否"} / {sandboxClickExecution.writes_remote ? "是" : "否"} / {sandboxClickExecution.executes_clicks ? "是" : "否"}</Descriptions.Item>
                                          <Descriptions.Item label="允许域名">{sandboxClickExecution.allowed_domains.join("；") || "-"}</Descriptions.Item>
                                          <Descriptions.Item label="汇总">{`跳转=${sandboxClickExecution.interaction_summary.has_navigation ? "是" : "否"} / 交互=${sandboxClickExecution.interaction_summary.has_dom_interaction ? "是" : "否"} / 动效=${sandboxClickExecution.interaction_summary.has_animation ? "是" : "否"} / 点击=${sandboxClickExecution.interaction_summary.clicked_count}`}</Descriptions.Item>
                                          <Descriptions.Item label="阻塞">{sandboxClickExecution.blockers.length ? sandboxClickExecution.blockers.join("；") : "无"}</Descriptions.Item>
                                          <Descriptions.Item label="消息">{sandboxClickExecution.message}</Descriptions.Item>
                                        </Descriptions>
                                        <Table
                                          size="small"
                                          columns={sandboxExecutionColumns}
                                          dataSource={sandboxClickExecution.click_results}
                                          rowKey={(record) => `${record.selector}-${record.status}`}
                                          pagination={false}
                                        />
                                        <Space wrap>
                                          <Button loading={draftLoading} onClick={() => void handleSandboxClickDraft()} disabled={!sandboxClickExecution.click_results.length}>
                                            沙箱结果生成 dry-run 草稿
                                          </Button>
                                          <Typography.Text type="secondary">只映射评分字段，不触 AIDP。</Typography.Text>
                                        </Space>
                                      </Space>
                                    ) : null}
                                  </Space>
                                </Card>
                              </Space>
                            ) : null}
                          </Space>
                        </Card>
                        <Card title="媒体检查计划" size="small">
                          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                            <Alert
                              type="warning"
                              showIcon
                              message="没有多模态执行结果前，不把图片/视频计划当作判分"
                              description="这里仅把左图、视频和后续多模态/关键帧步骤组织出来；真正评分仍需视觉模型或人工复核。"
                            />
                            <Space wrap>
                              <Button loading={questionContextLoading} onClick={() => void handleMediaInspectionPlan()}>
                                生成媒体检查计划
                              </Button>
                              <Button
                                loading={questionContextLoading}
                                onClick={() => void handleMediaInspectionExecution()}
                                disabled={!mediaInspectionPlan?.media_resources.length}
                              >
                                执行媒体基础探测
                              </Button>
                              <Button
                                loading={questionContextLoading}
                                onClick={() => void handleVideoKeyframeExtraction()}
                                disabled={!mediaInspectionPlan?.media_resources.some((item) => item.material_type === "video")}
                              >
                                抽取/复用 3 帧关键帧
                              </Button>
                              <Button
                                loading={questionContextLoading}
                                onClick={() => void handleVideoKeyframeExtraction(5, true)}
                                disabled={!mediaInspectionPlan?.media_resources.some((item) => item.material_type === "video")}
                              >
                                低置信补抽 5 帧
                              </Button>
                              <Button
                                loading={questionContextLoading}
                                onClick={() => void handleMediaInspectionProvider()}
                                disabled={!mediaInspectionPlan?.media_resources.length}
                              >
                                调用做题 AI 生成媒体判断
                              </Button>
                              <Typography.Text type="secondary">基础探测和关键帧只产出输入材料；做题 AI 只生成结构化判断和 dry-run，不写远端。</Typography.Text>
                            </Space>
                            {mediaInspectionPlan ? (
                              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                <Descriptions bordered column={1} size="small">
                                  <Descriptions.Item label="结果">{mediaInspectionPlan.ok ? "通过" : "阻塞"}</Descriptions.Item>
                                  <Descriptions.Item label="触网/写远端/已判分">{mediaInspectionPlan.sends_network ? "是" : "否"} / {mediaInspectionPlan.writes_remote ? "是" : "否"} / {mediaInspectionPlan.claims_visual_judgement ? "是" : "否"}</Descriptions.Item>
                                  <Descriptions.Item label="阻塞">{mediaInspectionPlan.blockers.length ? mediaInspectionPlan.blockers.join("；") : "无"}</Descriptions.Item>
                                  <Descriptions.Item label="消息">{mediaInspectionPlan.message}</Descriptions.Item>
                                </Descriptions>
                                <Table
                                  size="small"
                                  columns={mediaResourceColumns}
                                  dataSource={mediaInspectionPlan.media_resources}
                                  rowKey={(record) => record.key}
                                  pagination={false}
                                />
                                <Table
                                  size="small"
                                  columns={mediaStepColumns}
                                  dataSource={mediaInspectionPlan.inspection_steps}
                                  rowKey={(record) => record.key}
                                  pagination={false}
                                />
                                {mediaInspectionExecution ? (
                                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                    <Descriptions bordered column={1} size="small">
                                      <Descriptions.Item label="基础探测">{mediaInspectionExecution.ok ? "通过" : "阻塞"}</Descriptions.Item>
                                      <Descriptions.Item label="触网/写远端/已判分">
                                        {mediaInspectionExecution.sends_network ? "是" : "否"} / {mediaInspectionExecution.writes_remote ? "是" : "否"} / {mediaInspectionExecution.claims_visual_judgement ? "是" : "否"}
                                      </Descriptions.Item>
                                      <Descriptions.Item label="阻塞">{mediaInspectionExecution.blockers.length ? mediaInspectionExecution.blockers.join("；") : "无"}</Descriptions.Item>
                                      <Descriptions.Item label="消息">{mediaInspectionExecution.message}</Descriptions.Item>
                                    </Descriptions>
                                    <Table
                                      size="small"
                                      columns={mediaProbeColumns}
                                      dataSource={mediaInspectionExecution.probe_results}
                                      rowKey={(record) => record.key}
                                      pagination={false}
                                    />
                                  </Space>
                                ) : null}
                                {videoKeyframes ? (
                                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                    <Descriptions bordered column={1} size="small">
                                      <Descriptions.Item label="关键帧抽取">{videoKeyframes.ok ? "通过" : "阻塞"}</Descriptions.Item>
                                      <Descriptions.Item label="触网/写远端/已判分">
                                        {videoKeyframes.sends_network ? "是" : "否"} / {videoKeyframes.writes_remote ? "是" : "否"} / {videoKeyframes.claims_visual_judgement ? "是" : "否"}
                                      </Descriptions.Item>
                                      <Descriptions.Item label="Helper">{videoKeyframes.helper_mode || videoKeyframes.helper_endpoint}</Descriptions.Item>
                                      <Descriptions.Item label="缓存复用">{videoKeyframes.cache_hit ? "命中本地多帧 manifest" : "未命中，已调用 helper 抽帧"}</Descriptions.Item>
                                      <Descriptions.Item label="帧数">
                                        {videoKeyframes.keyframe_results.map((item) => `${item.resource_key}:${item.keyframes.length}`).join("；") || "0"}
                                      </Descriptions.Item>
                                      <Descriptions.Item label="证据归档">
                                        {videoKeyframes.archived_frame_count ? `${videoKeyframes.archived_frame_count} 帧；${videoKeyframes.artifact_path}` : "未归档"}
                                      </Descriptions.Item>
                                      <Descriptions.Item label="阻塞">{videoKeyframes.blockers.length ? videoKeyframes.blockers.join("；") : "无"}</Descriptions.Item>
                                      <Descriptions.Item label="消息">{videoKeyframes.message}</Descriptions.Item>
                                    </Descriptions>
                                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 12 }}>
                                      {videoKeyframes.keyframe_results.flatMap((item) => item.keyframes.map((frame) => (
                                        <div key={`${item.resource_key}-${frame.index}`} style={{ minWidth: 0 }}>
                                          <Image
                                            src={frame.preview_url || frame.data_url}
                                            alt={`${item.resource_key} 第 ${frame.index + 1} 帧`}
                                            width="100%"
                                            height={90}
                                            style={{ objectFit: "cover", borderRadius: 4, border: "1px solid #d9d9d9" }}
                                          />
                                          <Typography.Text style={{ display: "block", marginTop: 4 }} ellipsis>
                                            {item.resource_key} / {frame.timestamp_sec.toFixed(2)}s
                                          </Typography.Text>
                                          <Typography.Text type="secondary" style={{ display: "block" }} ellipsis>
                                            {frame.artifact_path || "未归档"}
                                          </Typography.Text>
                                        </div>
                                      )))}
                                    </div>
                                  </Space>
                                ) : null}
                                {mediaInspectionProvider ? (
                                  <Descriptions bordered column={1} size="small">
                                    <Descriptions.Item label="做题 AI">{mediaInspectionProvider.ok ? "通过" : "阻塞"}</Descriptions.Item>
                                    <Descriptions.Item label="上游 AI 状态">{mediaInspectionProvider.provider_status}</Descriptions.Item>
                                    <Descriptions.Item label="触网/写远端/已判分">
                                      {mediaInspectionProvider.sends_network ? "是" : "否"} / {mediaInspectionProvider.writes_remote ? "是" : "否"} / {mediaInspectionProvider.claims_visual_judgement ? "是" : "否"}
                                    </Descriptions.Item>
                                    <Descriptions.Item label="上游 AI 耗时">
                                      {mediaInspectionProvider.provider_call_count ?? 0} 次；上游 AI 往返 {mediaInspectionProvider.provider_elapsed_ms ?? 0} 毫秒；总计 {mediaInspectionProvider.total_elapsed_ms ?? 0} 毫秒
                                    </Descriptions.Item>
                                    <Descriptions.Item label="上游 AI 输入">
                                      文本 {mediaInspectionProvider.provider_input_text_chars ?? 0} 字；图片 {mediaInspectionProvider.provider_input_image_count ?? 0} 张；关键帧 {mediaInspectionProvider.provider_input_keyframe_count ?? 0} 张
                                    </Descriptions.Item>
                                    <Descriptions.Item label="上游 AI 诊断建议">
                                      {mediaInspectionProvider.provider_diagnostics?.length ? (
                                        <Space direction="vertical" size="small">
                                          {mediaInspectionProvider.provider_diagnostics.map((item) => (
                                            <span key={item.key}>
                                              {item.title}：{item.detail}；建议：{item.suggestion}
                                            </span>
                                          ))}
                                        </Space>
                                      ) : (
                                        "暂无诊断"
                                      )}
                                    </Descriptions.Item>
                                    <Descriptions.Item label="低置信补帧">
                                      {mediaInspectionProvider.supplement_attempted
                                        ? `${mediaInspectionProvider.supplement_status || "已尝试"}；${mediaInspectionProvider.supplement_keyframes?.archived_frame_count ?? 0} 帧；${mediaInspectionProvider.supplement_keyframes?.artifact_path || "-"}`
                                        : "未触发"}
                                    </Descriptions.Item>
                                    {mediaInspectionProvider.initial_video_keyframe_judgements.length ? (
                                      <Descriptions.Item label="初始判断">
                                        {mediaInspectionProvider.initial_video_keyframe_judgements.map((item) => `${item.resource_key}:${item.supporting_frame_count}/${item.total_frame_count} ${item.confidence}`).join("；")}
                                      </Descriptions.Item>
                                    ) : null}
                                    <Descriptions.Item label="图片判断">{mediaInspectionProvider.image_judgement.reason || "-"}</Descriptions.Item>
                                    <Descriptions.Item label="视频判断">
                                      <Space direction="vertical" size="small" style={{ width: "100%" }}>
                                        {mediaInspectionProvider.video_keyframe_judgements.map((item) => (
                                          <div key={item.resource_key}>
                                            <Space wrap size="small">
                                              <Typography.Text strong>{item.resource_key}</Typography.Text>
                                              <Tag color={item.confidence === "high" ? "green" : item.confidence === "medium" ? "blue" : "orange"}>
                                                {item.confidence || "unknown"}
                                              </Tag>
                                              <Tag color={item.review_required ? "red" : "green"}>
                                                {item.review_required ? "需复核" : "无需复核"}
                                              </Tag>
                                              <Typography.Text type="secondary">
                                                投票 {item.supporting_frame_count}/{item.total_frame_count}
                                              </Typography.Text>
                                            </Space>
                                            <Typography.Text style={{ display: "block" }}>
                                              {item.reason || item.keyframe_summary || "-"}
                                            </Typography.Text>
                                            {item.review_hint ? (
                                              <Typography.Text type="warning" style={{ display: "block" }}>
                                                {item.review_hint}
                                              </Typography.Text>
                                            ) : null}
                                          </div>
                                        ))}
                                      </Space>
                                    </Descriptions.Item>
                                    <Descriptions.Item label="阻塞">{mediaInspectionProvider.blockers.length ? mediaInspectionProvider.blockers.join("；") : "无"}</Descriptions.Item>
                                    <Descriptions.Item label="消息">{mediaInspectionProvider.message}</Descriptions.Item>
                                  </Descriptions>
                                ) : null}
                                <Card title="多模态/关键帧结果合并" size="small">
                                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                                    <Input.TextArea rows={9} value={mediaJudgementJson} onChange={(event) => setMediaJudgementJson(event.target.value)} />
                                    <Space wrap>
                                      <Button loading={draftLoading} onClick={() => void handleMediaInspectionDraft()}>
                                        媒体判断生成 dry-run 草稿
                                      </Button>
                                      <Typography.Text type="secondary">只合并结构化判断，不调用 AIDP。</Typography.Text>
                                    </Space>
                                  </Space>
                                </Card>
                              </Space>
                            ) : null}
                          </Space>
                        </Card>
                      </Space>
                    </Card>
                  ) : null}
                  <Card title="AI 评分与原因规则" size="small">
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <Table
                        size="small"
                        columns={ruleColumns}
                        dataSource={capability.scoring_rules}
                        rowKey={(record) => `score-${record.key}`}
                        pagination={false}
                      />
                      <Table
                        size="small"
                        columns={ruleColumns}
                        dataSource={capability.reason_rules}
                        rowKey={(record) => `reason-${record.key}`}
                        pagination={false}
                      />
                    </Space>
                  </Card>
                <Card title="AI 输出格式" size="small">
                    <Table
                      size="small"
                      columns={outputSchemaColumns}
                      dataSource={capability.ai_output_schema}
                      rowKey={(record) => record.field}
                      pagination={false}
                    />
                  </Card>
                  <Form layout="vertical">
                    <Form.Item label="上游做题 AI 提示">
                      <Input.TextArea rows={3} value={providerPrompt} onChange={(event) => setProviderPrompt(event.target.value)} />
                    </Form.Item>
                    <Space wrap>
                      <Button loading={draftLoading} onClick={() => void handleProviderDraftDryRun(false)}>本地做题 AI dry-run</Button>
                      <Button type="primary" loading={draftLoading} onClick={() => void handleProviderDraftDryRun(true)}>调用上游做题 AI dry-run</Button>
                    </Space>
                  </Form>
                  {draftResult?.ai_review_preview ? (
                    <Card title="上游 AI 输出复核" size="small">
                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                        <Descriptions bordered size="small" column={1}>
                          <Descriptions.Item label="上游 AI 状态">{draftResult.ai_review_preview.provider_status}</Descriptions.Item>
                          <Descriptions.Item label="下一步">{draftResult.ai_review_preview.next_step}</Descriptions.Item>
                        </Descriptions>
                        {draftResult.ai_review_preview.warnings.length ? (
                          <Alert type="warning" showIcon message="AI 输出需要注意" description={draftResult.ai_review_preview.warnings.join("；")} />
                        ) : (
                          <Alert type="info" showIcon message="AI 输出只作为草稿复核" description="请核对分数、原因、一致性和废弃判断；确认后再决定是否受控暂存。" />
                        )}
                        <Table
                          size="small"
                          columns={reviewColumns}
                          dataSource={draftResult.ai_review_preview.review_items}
                          rowKey={(record) => record.key}
                          pagination={false}
                        />
                        <Descriptions bordered size="small" column={1}>
                          <Descriptions.Item label="AI 原始输出">
                            <Typography.Text copyable>{JSON.stringify(draftResult.ai_review_preview.ai_output, null, 2)}</Typography.Text>
                          </Descriptions.Item>
                          <Descriptions.Item label="映射后草稿字段">
                            <Typography.Text copyable>{JSON.stringify(draftResult.ai_review_preview.mapped_answer_data, null, 2)}</Typography.Text>
                          </Descriptions.Item>
                        </Descriptions>
                        <Form layout="vertical">
                          <Form.Item label="人工复核备注">
                            <Input.TextArea rows={3} value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} />
                          </Form.Item>
                          <Button type="primary" loading={draftLoading} onClick={() => void handleReviewApproval()}>
                            人工复核通过，生成确认单
                          </Button>
                        </Form>
                      </Space>
                    </Card>
                  ) : null}
                  {reviewApproval ? (
                    <Card title={reviewApproval.confirmation_sheet.title} size="small">
                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                        <Alert
                          type="success"
                          showIcon
                          message={reviewApproval.message}
                          description="确认单不等于真实暂存；它只记录人工复核已通过，并列出后续真实暂存必须满足的闸门。"
                        />
                        <Descriptions bordered size="small" column={1}>
                          <Descriptions.Item label="状态">{reviewApproval.confirmation_sheet.status}</Descriptions.Item>
                          <Descriptions.Item label="复核人">{reviewApproval.confirmation_sheet.reviewer}</Descriptions.Item>
                          <Descriptions.Item label="复核备注">{reviewApproval.confirmation_sheet.review_note || "-"}</Descriptions.Item>
                          <Descriptions.Item label="触网/写远端">{reviewApproval.sends_network ? "是" : "否"} / {reviewApproval.writes_remote ? "是" : "否"}</Descriptions.Item>
                          <Descriptions.Item label="允许接口">{reviewApproval.confirmation_sheet.allowed_endpoint}</Descriptions.Item>
                          <Descriptions.Item label="必需闸门">{reviewApproval.confirmation_sheet.required_gates.join("；")}</Descriptions.Item>
                          <Descriptions.Item label="禁止动作">{reviewApproval.confirmation_sheet.forbidden_actions.join("；")}</Descriptions.Item>
                          <Descriptions.Item label="证据文件">{reviewApproval.confirmation_sheet.draft_evidence_path}</Descriptions.Item>
                          <Descriptions.Item label="确认文本">{reviewApproval.confirmation_sheet.confirm_text}</Descriptions.Item>
                          <Descriptions.Item label="下一步">{reviewApproval.confirmation_sheet.next_step}</Descriptions.Item>
                          <Descriptions.Item label="确认单草稿字段">
                            <Typography.Text copyable>{JSON.stringify(reviewApproval.confirmation_sheet.mapped_answer_data, null, 2)}</Typography.Text>
                          </Descriptions.Item>
                        </Descriptions>
                        <Card title="真实暂存前操作员闸门" size="small">
                          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                            <Alert
                              type={reviewApproval.confirmation_sheet.ready_for_gated_write ? "success" : "warning"}
                              showIcon
                              message={reviewApproval.confirmation_sheet.ready_for_gated_write ? "真实草稿暂存闸门已就绪" : "真实草稿暂存仍被后端闸门拦截"}
                              description={reviewApproval.confirmation_sheet.ready_for_gated_write ? "仍需确认字段 diff 后再执行；执行后必须人工打开页面复核。" : "当前不会写入 AIDP。请先确认字段 diff，若确需写草稿，再显式开启 AIDP_TEMP_DRAFT_ALLOW_WRITE=1。"}
                            />
                            <Table
                              size="small"
                              columns={gateColumns}
                              dataSource={reviewApproval.confirmation_sheet.gate_statuses}
                              rowKey={(record) => record.key}
                              pagination={false}
                            />
                            <Table
                              size="small"
                              columns={fieldDiffColumns}
                              dataSource={reviewApproval.confirmation_sheet.field_diff}
                              rowKey={(record) => record.field}
                              pagination={false}
                            />
                            <Typography.Title level={5} style={{ margin: 0 }}>真实暂存演练检查清单</Typography.Title>
                            <Table
                              size="small"
                              columns={rehearsalChecklistColumns}
                              dataSource={reviewApproval.confirmation_sheet.rehearsal_checklist}
                              rowKey={(record) => record.key}
                              pagination={false}
                            />
                          </Space>
                        </Card>
                        <Space wrap>
                          <Button danger loading={draftLoading} disabled={!reviewApproval.confirmation_sheet.ready_for_gated_write} onClick={handleConfirmationExecute}>
                            按确认单字段受控执行草稿暂存
                          </Button>
                          <Typography.Text type="secondary">
                            {reviewApproval.confirmation_sheet.ready_for_gated_write ? "执行前请逐项核对字段 diff。" : "环境闸门未开启，按钮保持禁用。"}
                          </Typography.Text>
                        </Space>
                      </Space>
                    </Card>
                  ) : null}
                  <Table
                    size="small"
                    columns={fieldColumns}
                    dataSource={capability.field_mappings}
                    rowKey={(record) => record.field}
                    pagination={false}
                  />
                  <Form layout="vertical">
                    <Form.Item label="AI 输出 JSON（平台会映射到草稿字段）">
                      <Input.TextArea rows={9} value={aiDraftJson} onChange={(event) => setAiDraftJson(event.target.value)} />
                    </Form.Item>
                    <Button loading={draftLoading} onClick={() => void handleAiDraftDryRun()}>AI 输出生成 dry-run 草稿</Button>
                  </Form>
                  <Form layout="vertical">
                    <Form.Item label="草稿字段 JSON（只允许能力卡字段）">
                      <Input.TextArea rows={7} value={draftJson} onChange={(event) => setDraftJson(event.target.value)} />
                    </Form.Item>
                    <Space wrap>
                      <Button loading={draftLoading} onClick={() => void handleDraftDryRun()}>生成 dry-run 草稿</Button>
                      <Button danger loading={draftLoading} onClick={handleDraftExecute}>受控执行草稿暂存</Button>
                    </Space>
                  </Form>
                  {draftResult ? (
                    <Descriptions bordered column={1} size="small">
                      <Descriptions.Item label="结果">{draftResult.ok ? "通过" : "失败/阻塞"}</Descriptions.Item>
                      <Descriptions.Item label="模式">{draftResult.mode}</Descriptions.Item>
                      <Descriptions.Item label="触网/写远端">{draftResult.sends_network ? "是" : "否"} / {draftResult.writes_remote ? "是" : "否"}</Descriptions.Item>
                      <Descriptions.Item label="BaseResp">{draftResult.base_resp_status_code ?? "-"}</Descriptions.Item>
                      <Descriptions.Item label="阻塞项">{draftResult.blockers.length ? draftResult.blockers.join(", ") : "无"}</Descriptions.Item>
                      <Descriptions.Item label="证据文件">{draftResult.evidence_path ?? "-"}</Descriptions.Item>
                      <Descriptions.Item label="消息">{draftResult.message}</Descriptions.Item>
                    </Descriptions>
                  ) : null}
                </Space>
              ) : (
                <Alert type="info" showIcon message="该任务还没有可用能力卡" description="先去 AI 标注能力工作台制作能力；录制学习包只作为字段学习来源，不能替代 Step3/Step4 闸门。" />
              )}
            </Card>
                    </div>
                  ),
                },
              ]}
            />
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}
