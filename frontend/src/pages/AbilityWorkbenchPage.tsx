import { Alert, Button, Card, Col, Descriptions, Empty, Image, Input, Row, Select, Space, Spin, Steps, Table, Tabs, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";

import {
  approveTaskAbilityVersion,
  checkTaskAutoRunPreflight,
  createTaskAbilityPromptSnapshot,
  createTaskAbilityReplayReport,
  fetchAccounts,
  fetchLatestTaskLiveHttpTest,
  fetchSubmittedHistory,
  fetchSubmittedHistoryStats,
  fetchTaskAbilityDrafts,
  fetchTaskLearningPackages,
  fetchTaskAbilityPromptSnapshots,
  fetchTaskAbilityReplay,
  fetchTaskAbilityRunGate,
  fetchTaskAutoRun,
  fetchTaskAutoRunWorkerStatus,
  fetchTaskCatalog,
  fetchTestset,
  generateTestset,
  restoreTaskAbilityPromptSnapshot,
  runTaskLiveHttpTest,
  saveSelectedLearningPackage,
  saveTestset,
  sendTaskAbilityChat,
  startTaskAbilityProductionRun,
  startTaskAbilityTrialRun,
  stopTaskAutoRun,
  stopTaskAutoRunWorker,
  syncSubmittedHistory,
  type AccountItem,
  type AiChatMessage,
  type LearningPackageItem,
  type SubmittedHistorySample,
  type SubmittedHistoryStatsResponse,
  type TaskAbilityDraftItem,
  type TaskAbilityReplayCard,
  type TaskAbilityPromptSnapshotResponse,
  type TaskAbilityReplayResponse,
  type TaskAbilityRunConfig,
  type TaskAbilityRunGateResponse,
  type TaskAutoRunResponse,
  type TaskAutoRunPreflightResponse,
  type TaskAutoRunWorkerStatusResponse,
  type TaskCatalogItem,
  type TaskLiveHttpTestResponse,
  type TestsetGenerateResponse,
  type TestsetRead,
  updateTaskAbilityRunConfig,
  updateTaskAbilityDraft,
} from "../api/client";

type StepIndex = 0 | 1 | 2 | 3;
type DraftFormState = { system_ai_draft: string };

function parsePendingCount(value: string) {
  const normalized = String(value || "").replace(/,/g, "");
  const match = normalized.match(/\d+/);
  return match ? Number(match[0]) : 0;
}

function dedupeTaskCatalogItems(items: TaskCatalogItem[]) {
  const selected = new Map<string, TaskCatalogItem>();
  for (const item of items) {
    const key = [item.source_account_user_id, item.task_id || item.task_name_id || item.raw_task_name].join("::");
    const existing = selected.get(key);
    if (!existing) {
      selected.set(key, item);
      continue;
    }
    const seenAt = item.last_task_page_seen_at ? new Date(item.last_task_page_seen_at).getTime() : 0;
    const existingSeenAt = existing.last_task_page_seen_at ? new Date(existing.last_task_page_seen_at).getTime() : 0;
    const pending = parsePendingCount(item.pending_raw);
    const existingPending = parsePendingCount(existing.pending_raw);
    if (seenAt > existingSeenAt || (seenAt === existingSeenAt && pending > existingPending)) {
      selected.set(key, item);
    }
  }
  return Array.from(selected.values()).sort((left, right) => {
    const pendingDelta = parsePendingCount(right.pending_raw) - parsePendingCount(left.pending_raw);
    if (pendingDelta !== 0) return pendingDelta;
    return (right.last_task_page_seen_at || "").localeCompare(left.last_task_page_seen_at || "");
  });
}

function safeError(error: unknown): string {
  return error instanceof Error ? error.message : "接口请求失败";
}

function learningPackageSourceLabel(source: string) {
  if (source === "browser_extension") return "插件上传";
  if (source === "local_assistant") return "本机助手";
  return source || "未知来源";
}

function learningPackageStateLabel(item: LearningPackageItem) {
  const completeness = String(item.completeness || "").toLowerCase();
  const status = String(item.status || "").toLowerCase();
  if (status === "failed") return "解析失败";
  if (status === "processing" || status === "parsing") return "解析中";
  if (status === "parsed" && completeness === "complete") return "已解析（完整）";
  if (status === "parsed" && completeness === "partial") return "已解析（部分完整）";
  if (completeness === "complete") return "完整";
  if (completeness === "partial") return "部分完整";
  return status || "未解析";
}

function taskAccountIdsForTask(taskId: string, items: TaskCatalogItem[]) {
  const ids = new Set<string>();
  for (const item of items) {
    if (item.task_id === taskId && item.source_account_user_id) {
      ids.add(item.source_account_user_id);
    }
  }
  return Array.from(ids);
}

function hasRunId(value: unknown): value is { run_id: string } {
  return Boolean(value) && typeof value === "object" && typeof (value as { run_id?: unknown }).run_id === "string" && Boolean((value as { run_id: string }).run_id);
}

function summarizeSubmittedSample(record: SubmittedHistorySample) {
  const primary = record.primary_output as Record<string, unknown>;
  const answer = primary.answer as Record<string, unknown> | undefined;
  const item = primary.item as Record<string, unknown> | undefined;
  const data = primary.data as Record<string, unknown> | undefined;
  const scoreMap = (data?.label_sorce as Record<string, unknown> | undefined) || {};
  const reasonMap = (data?.label_remark as Record<string, unknown> | undefined) || {};
  const scoreSummary = Object.entries(scoreMap).map(([key, value]) => `${key}:${String(value)}`).join("；");
  const reasonSummary = Object.entries(reasonMap).map(([key, value]) => `${key}:${String(value)}`).join("；");
  const summary = String(answer?.reason || reasonSummary || answer?.score || scoreSummary || item?.uid || "已同步样本");
  return summary.slice(0, 160);
}

function replayStatusTag(value: string) {
  if (value === "matched") return <Tag color="green">一致</Tag>;
  if (value === "different") return <Tag color="gold">有差异</Tag>;
  if (value === "error") return <Tag color="red">回放失败</Tag>;
  return <Tag>{value}</Tag>;
}

function formatReplayPreview(preview: Record<string, unknown>) {
  const scoreEntries = Object.entries(preview).filter(([key]) => key.includes("label_sorce"));
  const reasonEntries = Object.entries(preview).filter(([key]) => key.includes("label_remark"));
  const scoreText = scoreEntries.map(([, value]) => String(value)).join(" / ");
  const reasonText = reasonEntries.map(([, value]) => String(value)).join("\n");
  if (!scoreText && !reasonText) return "-";
  if (scoreText && reasonText) return `评分 ${scoreText}\n${reasonText}`;
  if (scoreText) return `评分 ${scoreText}`;
  return reasonText;
}

function extractCompletePromptDraft(messageText: string) {
  const text = messageText.trim();
  const candidates: string[] = [];
  const blockPattern = /```(?:markdown|md|text)?\s*([\s\S]*?)```/gi;
  let match = blockPattern.exec(text);
  while (match) {
    candidates.push(match[1].trim());
    match = blockPattern.exec(text);
  }
  if (!candidates.length && text.startsWith("# ")) {
    candidates.push(text);
  }
  return (
    candidates.find((candidate) => (
      candidate.includes("## 适用任务")
      && candidate.includes("## 读题材料")
      && candidate.includes("## 输出格式")
      && candidate.includes("data.label_sorce")
      && candidate.includes("data.label_remark")
    )) || ""
  );
}

function buildDraftPatchFromAssistantMessage(messageText: string, currentDraft: DraftFormState) {
  const promptDraft = extractCompletePromptDraft(messageText);
  if (!promptDraft) return null;
  return { ...currentDraft, system_ai_draft: promptDraft };
}

function buildFieldDiffRows(currentDraft: { system_ai_draft: string }, snapshot: TaskAbilityPromptSnapshotResponse | null) {
  if (!snapshot) return [];
  return [
    { field: "system_ai_draft", title: "Prompt 草稿", current: currentDraft.system_ai_draft, snapshot: snapshot.system_ai_draft },
  ].filter((item) => item.current !== item.snapshot);
}

export function AbilityWorkbenchPage() {
  const [loading, setLoading] = useState(false);
  const [syncLoading, setSyncLoading] = useState(false);
  const [testsetLoading, setTestsetLoading] = useState(false);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [approveLoading, setApproveLoading] = useState(false);
  const [runConfigSaving, setRunConfigSaving] = useState(false);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [trialLoading, setTrialLoading] = useState(false);
  const [productionLoading, setProductionLoading] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [applySuggestionLoading, setApplySuggestionLoading] = useState(false);
  const [replayLoading, setReplayLoading] = useState(false);
  const [runControlLoading, setRunControlLoading] = useState(false);
  const [tasks, setTasks] = useState<TaskCatalogItem[]>([]);
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [drafts, setDrafts] = useState<TaskAbilityDraftItem[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [submittedStats, setSubmittedStats] = useState<SubmittedHistoryStatsResponse | null>(null);
  const [submittedSamples, setSubmittedSamples] = useState<SubmittedHistorySample[]>([]);
  const [testset, setTestset] = useState<TestsetRead | null>(null);
  const [generatedTestset, setGeneratedTestset] = useState<TestsetGenerateResponse | null>(null);
  const [promptSnapshots, setPromptSnapshots] = useState<TaskAbilityPromptSnapshotResponse[]>([]);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState("");
  const [liveTestLoading, setLiveTestLoading] = useState(false);
  const [liveAccountId, setLiveAccountId] = useState("");
  const [liveTestResult, setLiveTestResult] = useState<TaskLiveHttpTestResponse | null>(null);
  const [learningPackages, setLearningPackages] = useState<LearningPackageItem[]>([]);
  const [selectedLearningPackageId, setSelectedLearningPackageId] = useState("");
  const [learningPackageSaving, setLearningPackageSaving] = useState(false);
  const [managedRun, setManagedRun] = useState<TaskAutoRunResponse | null>(null);
  const [managedWorkerStatus, setManagedWorkerStatus] = useState<TaskAutoRunWorkerStatusResponse | null>(null);
  const [runGate, setRunGate] = useState<TaskAbilityRunGateResponse | null>(null);
  const [runAccountIds, setRunAccountIds] = useState<string[]>([]);
  const [preflightResult, setPreflightResult] = useState<TaskAutoRunPreflightResponse | null>(null);
  const [currentStep, setCurrentStep] = useState<StepIndex>(0);
  const [draftForm, setDraftForm] = useState<DraftFormState>({ system_ai_draft: "" });
  const [draftUndoStack, setDraftUndoStack] = useState<DraftFormState[]>([]);
  const [chatMessages, setChatMessages] = useState<AiChatMessage[]>([{ role: "assistant", content: "这里是系统 AI 对话区。把规则、边界、样例和判分要求发给我，我会整理成可直接保存的完整 Prompt 建议。" }]);
  const [chatInput, setChatInput] = useState("");
  const [replayResult, setReplayResult] = useState<TaskAbilityReplayResponse | null>(null);
  const [replayReportMeta, setReplayReportMeta] = useState<{ report_id: string; created_at: string; path: string } | null>(null);
  const [selectedReplayUid, setSelectedReplayUid] = useState("");
  const [runConfig, setRunConfig] = useState<TaskAbilityRunConfig>({
    mode: "safe",
    rate_limit_per_minute: 5,
    trial_max_items_per_account: 3,
    production_max_items_per_account: 50,
    consecutive_fail_threshold: 3,
  });
  const [syncConfig, setSyncConfig] = useState({ account_id: "", node_id: 1, sample_count: 10 });

  const selectedTask = useMemo(
    () => tasks.find((item) => item.task_id === selectedTaskId) ?? null,
    [tasks, selectedTaskId],
  );

  const selectedDraft = useMemo(
    () => drafts.find((item) => item.task_id === selectedTaskId) ?? null,
    [drafts, selectedTaskId],
  );

  const taskScopedAccountIds = useMemo(
    () => taskAccountIdsForTask(selectedTaskId, tasks),
    [selectedTaskId, tasks],
  );

  const selectedSnapshot = useMemo(
    () => promptSnapshots.find((item) => item.snapshot_id === selectedSnapshotId) ?? null,
    [promptSnapshots, selectedSnapshotId],
  );

  const snapshotDiffRows = useMemo(
    () => buildFieldDiffRows(draftForm, selectedSnapshot),
    [draftForm, selectedSnapshot],
  );

  const runAccountOptions = useMemo(() => {
    const preferredIds = taskScopedAccountIds.length ? taskScopedAccountIds : accounts.map((account) => account.user_id);
    return preferredIds
      .map((userId) => {
        const account = accounts.find((item) => item.user_id === userId);
        return {
          value: userId,
          label: `${account?.display_name || account?.custom_name || userId} / ${userId}`,
        };
      })
      .filter((item, index, array) => array.findIndex((candidate) => candidate.value === item.value) === index);
  }, [accounts, taskScopedAccountIds]);

  const liveAccountOptions = useMemo(
    () => runAccountOptions.length ? runAccountOptions : accounts.map((account) => ({ value: account.user_id, label: `${account.display_name || account.custom_name || account.user_id} / ${account.user_id}` })),
    [accounts, runAccountOptions],
  );

  const loadStep2Artifacts = async (taskId: string) => {
    const replay = await fetchTaskAbilityReplay(taskId).catch(() => null);
    setReplayResult(replay);
    setReplayReportMeta(null);
    const firstReadyUid = replay?.items?.find((item) => item.compare_status !== "error")?.uid || replay?.items?.[0]?.uid || "";
    const nextReplayUid = selectedReplayUid || firstReadyUid;
    if (nextReplayUid && nextReplayUid !== selectedReplayUid) {
      setSelectedReplayUid(nextReplayUid);
    }
  };

  const loadTaskArtifacts = async (taskId: string) => {
    const [stats, history, nextTestset, snapshots, gate, latestLive, packageState] = await Promise.all([
      fetchSubmittedHistoryStats(taskId).catch(() => null),
      fetchSubmittedHistory(taskId).catch(() => null),
      fetchTestset(taskId).catch(() => null),
      fetchTaskAbilityPromptSnapshots(taskId).catch(() => null),
      fetchTaskAbilityRunGate(taskId).catch(() => null),
      fetchLatestTaskLiveHttpTest(taskId).catch(() => null),
      fetchTaskLearningPackages(taskId).catch(() => null),
    ]);
    setSubmittedStats(stats);
    setSubmittedSamples(history?.items ?? []);
    setTestset(nextTestset);
    setGeneratedTestset(null);
    const snapshotItems = snapshots?.items ?? [];
    setPromptSnapshots(snapshotItems);
    setSelectedSnapshotId((current) => snapshotItems.some((item) => item.snapshot_id === current) ? current : (snapshotItems[0]?.snapshot_id || ""));
    setRunGate(gate);
    setLiveTestResult(latestLive);
    setLearningPackages(packageState?.items ?? []);
    setSelectedLearningPackageId(packageState?.selected_learning_package_id ?? "");
    setPreflightResult(null);
    const activeRunId = hasRunId(gate?.last_production_run) ? gate?.last_production_run.run_id : (hasRunId(gate?.last_trial_run) ? gate?.last_trial_run.run_id : "");
    if (activeRunId) {
      const [run, workerStatus] = await Promise.all([
        fetchTaskAutoRun(activeRunId).catch(() => null),
        fetchTaskAutoRunWorkerStatus(activeRunId).catch(() => null),
      ]);
      setManagedRun(run);
      setManagedWorkerStatus(workerStatus);
    } else {
      setManagedRun(null);
      setManagedWorkerStatus(null);
    }
    if (nextTestset?.sample_ids?.length) {
      setSelectedReplayUid((current) => current || nextTestset.sample_ids[0] || "");
    } else {
      setReplayResult(null);
      setSelectedReplayUid("");
    }
  };

  const load = async () => {
    setLoading(true);
    try {
      const [catalog, abilityDrafts, runtimeAccounts] = await Promise.all([fetchTaskCatalog(), fetchTaskAbilityDrafts(), fetchAccounts()]);
      const nextTasks = dedupeTaskCatalogItems(catalog.items);
      setTasks(nextTasks);
      setDrafts(abilityDrafts.items);
      setAccounts(runtimeAccounts);
      const nextTaskId = selectedTaskId || nextTasks[0]?.task_id || "";
      const nextTaskAccountIds = taskAccountIdsForTask(nextTaskId, nextTasks);
      setSelectedTaskId(nextTaskId);
      setLiveAccountId((current) => (current && runtimeAccounts.some((account) => account.user_id === current)) ? current : (nextTaskAccountIds[0] || runtimeAccounts[0]?.user_id || ""));
      setRunAccountIds((current) => {
        const kept = current.filter((item) => nextTaskAccountIds.includes(item));
        return kept.length ? kept : nextTaskAccountIds;
      });
      const nextDraft = abilityDrafts.items.find((item) => item.task_id === nextTaskId) ?? null;
      setDraftForm({
        system_ai_draft: nextDraft?.system_ai_draft || "",
      });
      if (nextTaskId) {
        await loadTaskArtifacts(nextTaskId);
      }
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setDraftForm({
      system_ai_draft: selectedDraft?.system_ai_draft || "",
    });
    setDraftUndoStack([]);
  }, [selectedDraft]);

  useEffect(() => {
    if (runGate?.run_config) {
      setRunConfig(runGate.run_config);
    }
  }, [runGate]);

  useEffect(() => {
    if (!selectedTaskId) return;
    if (taskScopedAccountIds.length) {
      setRunAccountIds((current) => {
        const kept = current.filter((item) => taskScopedAccountIds.includes(item));
        return kept.length ? kept : taskScopedAccountIds;
      });
      setLiveAccountId((current) => taskScopedAccountIds.includes(current) ? current : taskScopedAccountIds[0]);
      return;
    }
    setLiveAccountId((current) => current || accounts[0]?.user_id || "");
    setRunAccountIds((current) => current.length ? current : accounts.slice(0, 1).map((account) => account.user_id));
  }, [accounts, selectedTaskId, taskScopedAccountIds]);

  const handleTaskChange = async (taskId: string) => {
    setSelectedTaskId(taskId);
    const nextTaskAccountIds = taskAccountIdsForTask(taskId, tasks);
    setRunAccountIds(nextTaskAccountIds);
    setLiveAccountId(nextTaskAccountIds[0] || accounts[0]?.user_id || "");
    setSelectedReplayUid("");
    setReplayResult(null);
    setChatMessages([{ role: "assistant", content: "已切换任务。继续把当前任务的规则、样例和判分要求发给我，我会整理成新的完整 Prompt 建议。" }]);
    await loadTaskArtifacts(taskId);
  };

  const handleSyncSubmittedHistory = async (force = false) => {
    if (!selectedTaskId) return;
    setSyncLoading(true);
    try {
      const result = await syncSubmittedHistory(selectedTaskId, { force, account_id: syncConfig.account_id, node_id: syncConfig.node_id });
      await loadTaskArtifacts(selectedTaskId);
      message.success(`已同步已提交样本：${result.sample_count} 条，样本池 ${result.sample_pool_count} 条`);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setSyncLoading(false);
    }
  };

  const handleGenerateTestset = async (sampleCount = 10) => {
    if (!selectedTaskId) return;
    setTestsetLoading(true);
    try {
      const result = await generateTestset(selectedTaskId, { sample_count: sampleCount });
      setGeneratedTestset(result);
      message.success(`已生成固定测试集候选：${result.sample_count} 条`);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setTestsetLoading(false);
    }
  };

  const handleSaveTestset = async () => {
    if (!selectedTaskId || !generatedTestset) return;
    setTestsetLoading(true);
    try {
      const result = await saveTestset(selectedTaskId, { sample_ids: generatedTestset.sample_ids });
      setTestset(result);
      setSelectedReplayUid(result.sample_ids[0] || "");
      const [stats, history] = await Promise.all([
        fetchSubmittedHistoryStats(selectedTaskId).catch(() => null),
        fetchSubmittedHistory(selectedTaskId).catch(() => null),
      ]);
      setSubmittedStats(stats);
      setSubmittedSamples(history?.items ?? []);
      setGeneratedTestset(null);
      setReplayResult(null);
      setReplayReportMeta(null);
      message.success(`已固定测试集：${result.sample_count} 条`);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setTestsetLoading(false);
    }
  };

  const handleCreatePromptSnapshot = async () => {
    if (!selectedTaskId) return;
    setSnapshotLoading(true);
    try {
      const snapshot = await createTaskAbilityPromptSnapshot(selectedTaskId, { note: "工作台保存快照" });
      await loadTaskArtifacts(selectedTaskId);
      setSelectedSnapshotId(snapshot.snapshot_id);
      message.success(`已创建 Prompt 快照：${snapshot.snapshot_id}`);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setSnapshotLoading(false);
    }
  };

  const handleRestorePromptSnapshot = async () => {
    if (!selectedTaskId || !selectedSnapshotId) return;
    setSnapshotLoading(true);
    try {
      await restoreTaskAbilityPromptSnapshot(selectedTaskId, { snapshot_id: selectedSnapshotId });
      await load();
      message.success(`已恢复 Prompt 快照：${selectedSnapshotId}`);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setSnapshotLoading(false);
    }
  };

  const handleSaveDraft = async () => {
    if (!selectedDraft) return;
    try {
      await updateTaskAbilityDraft(selectedDraft.id, { system_ai_draft: draftForm.system_ai_draft });
      await load();
      message.success("已保存当前 Prompt / 草稿，并重置为待重新验证。");
    } catch (error: unknown) {
      message.error(safeError(error));
    }
  };

  const handleSendTaskChat = async () => {
    const text = chatInput.trim();
    if (!text || !selectedTaskId) return;
    const nextHistory = [...chatMessages, { role: "user", content: text }];
    setChatMessages(nextHistory);
    setChatInput("");
    setChatLoading(true);
    try {
      const result = await sendTaskAbilityChat(selectedTaskId, { message: text, history: nextHistory.slice(-8), use_provider: true, selected_learning_package_id: selectedLearningPackageId || undefined });
      setChatMessages([...nextHistory, { role: "assistant", content: `${result.answer}\n\n调用状态：${result.provider_status}` }]);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setChatLoading(false);
    }
  };

  const handleSelectLearningPackage = async (learningPackageId: string) => {
    if (!selectedTaskId) return;
    setLearningPackageSaving(true);
    try {
      const result = await saveSelectedLearningPackage(selectedTaskId, { selected_learning_package_id: learningPackageId });
      setSelectedLearningPackageId(result.selected_learning_package_id);
      setLearningPackages((current) => current.map((item) => ({ ...item, selected: item.learning_package_id === result.selected_learning_package_id })));
      message.success(result.message);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setLearningPackageSaving(false);
    }
  };

  const handleApplyLastAssistantSuggestion = async () => {
    const lastAssistant = [...chatMessages].reverse().find((item) => item.role === "assistant");
    if (!lastAssistant?.content) {
      message.error("当前没有可应用的 AI 建议。");
      return;
    }
    setApplySuggestionLoading(true);
    try {
      const nextDraft = buildDraftPatchFromAssistantMessage(lastAssistant.content, draftForm);
      if (!nextDraft) {
        message.error("未检测到完整 Prompt 草稿代码块，已拒绝自动写入，避免把对话解释混入做题 Prompt。");
        return;
      }
      setDraftUndoStack((current) => [...current, draftForm]);
      setDraftForm(nextDraft);
      message.success("已把最近一次 AI 建议应用到当前草稿，可继续人工微调后保存。");
    } finally {
      setApplySuggestionLoading(false);
    }
  };

  const handleUndoDraftSuggestion = () => {
    const previous = draftUndoStack[draftUndoStack.length - 1];
    if (!previous) {
      message.error("当前没有可撤销的草稿修改。");
      return;
    }
    setDraftForm(previous);
    setDraftUndoStack((current) => current.slice(0, -1));
    message.success("已撤销最近一次 AI 应用修改。");
  };

  const handleRefreshReplay = async () => {
    if (!selectedTaskId) return;
    setReplayLoading(true);
    try {
      const result = await createTaskAbilityReplayReport(selectedTaskId, {
        prompt_content: draftForm.system_ai_draft.trim() || undefined,
        sample_limit: 10,
      });
      setReplayResult(result);
      setReplayReportMeta({ report_id: result.report_id, created_at: result.created_at, path: result.path });
      const firstReadyUid = result.items.find((item) => item.compare_status !== "error")?.uid || result.items[0]?.uid || "";
      const nextUid = selectedReplayUid || firstReadyUid;
      setSelectedReplayUid(nextUid);
      message.success(result.message);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setReplayLoading(false);
    }
  };

  const handleSaveRunConfig = async () => {
    if (!selectedTaskId) return;
    setRunConfigSaving(true);
    try {
      const result = await updateTaskAbilityRunConfig(selectedTaskId, runConfig);
      setRunConfig(result.run_config);
      await loadTaskArtifacts(selectedTaskId);
      message.success(result.message);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setRunConfigSaving(false);
    }
  };

  const handlePauseManagedRun = async () => {
    if (!managedRun?.run_id) return;
    setRunControlLoading(true);
    try {
      const workerStatus = await stopTaskAutoRunWorker(managedRun.run_id);
      setManagedWorkerStatus(workerStatus);
      message.success("已暂停当前运行。");
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setRunControlLoading(false);
    }
  };

  const handleStopManagedRun = async () => {
    if (!managedRun?.run_id) return;
    setRunControlLoading(true);
    try {
      const stopped = await stopTaskAutoRun(managedRun.run_id);
      setManagedRun(stopped);
      const workerStatus = await stopTaskAutoRunWorker(managedRun.run_id).catch(() => null);
      setManagedWorkerStatus(workerStatus);
      await loadTaskArtifacts(selectedTaskId);
      message.success("已停止当前运行。");
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setRunControlLoading(false);
    }
  };

  const handleSelectReplaySample = async (uid: string) => {
    if (!selectedTaskId) return;
    setSelectedReplayUid(uid);
  };

  const handleRunLiveHttpTest = async () => {
    if (!selectedTaskId) return;
    setLiveTestLoading(true);
    try {
      const result = await runTaskLiveHttpTest(selectedTaskId, { account_user_id: liveAccountId, use_system_ai_for_vision: true });
      setLiveTestResult(result);
      await loadTaskArtifacts(selectedTaskId);
      message.success(result.saved_to_task_ui ? "Live 暂存验证已完成：已暂存、未正式提交。" : result.message);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setLiveTestLoading(false);
    }
  };

  const handleApproveAbilityVersion = async () => {
    if (!selectedTaskId) return;
    setApproveLoading(true);
    try {
      const result = await approveTaskAbilityVersion(selectedTaskId);
      await loadTaskArtifacts(selectedTaskId);
      message.success(result.message || "已批准当前能力版本。");
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setApproveLoading(false);
    }
  };

  const handleRunPreflight = async () => {
    if (!selectedTaskId || !runAccountIds.length) {
      message.error("先选择至少一个执行账号。");
      return;
    }
    setPreflightLoading(true);
    try {
      const result = await checkTaskAutoRunPreflight({ task_id: selectedTaskId, account_user_ids: runAccountIds, node_id: "1" });
      setPreflightResult(result);
      message.success(result.message);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setPreflightLoading(false);
    }
  };

  const handleStartTrialRun = async () => {
    if (!selectedTaskId || !runAccountIds.length) {
      message.error("先选择至少一个试运行账号。");
      return;
    }
    setTrialLoading(true);
    try {
      const result = await startTaskAbilityTrialRun(selectedTaskId, { account_user_ids: runAccountIds, node_id: "1", run_config: runConfig });
      await loadTaskArtifacts(selectedTaskId);
      message.success(`试运行已启动：${result.run_id}`);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setTrialLoading(false);
    }
  };

  const handleStartProductionRun = async () => {
    if (!selectedTaskId || !runAccountIds.length) {
      message.error("先选择至少一个生产运行账号。");
      return;
    }
    setProductionLoading(true);
    try {
      const result = await startTaskAbilityProductionRun(selectedTaskId, { account_user_ids: runAccountIds, node_id: "1", run_config: runConfig });
      await loadTaskArtifacts(selectedTaskId);
      message.success(`生产运行已启动：${result.run_id}`);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setProductionLoading(false);
    }
  };

  const testsetRows = (testset?.sample_ids ?? generatedTestset?.sample_ids ?? []).map((uid) => ({ uid }));
  const testsetColumns: ColumnsType<{ uid: string }> = [
    { title: "#", key: "index", render: (_, __, index) => String(index + 1).padStart(2, "0"), width: 60 },
    { title: "样本 ID", dataIndex: "uid", key: "uid" },
  ];

  const submittedColumns: ColumnsType<SubmittedHistorySample> = [
    { title: "样本 ID", dataIndex: "uid", key: "uid", width: 220 },
    { title: "ItemID", dataIndex: "item_id", key: "item_id", width: 180 },
    { title: "摘要", key: "summary", render: (_, record) => summarizeSubmittedSample(record) },
  ];

  const liveTestReportId = liveTestResult?.report_id || String(runGate?.live_test_report?.report_id || "");
  const lastTrialRun = hasRunId(runGate?.last_trial_run) ? runGate?.last_trial_run : null;
  const lastProductionRun = hasRunId(runGate?.last_production_run) ? runGate?.last_production_run : null;
  const replayCards = replayResult?.cards ?? [];

  return (
    <div className="page-stack">
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Card>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <div>
              <Typography.Title level={2} style={{ margin: 0 }}>AI 标注能力工作台</Typography.Title>
      <Typography.Text type="secondary">当前骨架已把 Step1-4 收到单页里；Step2 现在是正式三栏工作区，可直接做系统 AI 对话、完整 Prompt 编辑和固定测试集回放。</Typography.Text>
            </div>
            <Steps
              current={currentStep}
              onChange={(value) => setCurrentStep(value as StepIndex)}
              items={[
                { title: "获取测试样本", description: "同步已提交样本池并固定测试集。" },
                { title: "能力调教", description: "系统 AI / Prompt / 10 题回放。" },
                { title: "Live 暂存验证", description: "纯 HTTP 端到端不提交暂存测试。" },
                { title: "批准并运行", description: "人工批准能力版本后进入运行。" },
              ]}
            />
            <Descriptions bordered size="small" column={3}>
              <Descriptions.Item label="当前任务">
                <Select
                  style={{ width: 420 }}
                  value={selectedTaskId || undefined}
                  placeholder="选择任务"
                  options={tasks.map((item) => ({
                    value: item.task_id,
                    label: `${item.raw_task_name || item.task_short_name || item.task_id} / ${item.task_id}`,
                  }))}
                  onChange={(value) => void handleTaskChange(value)}
                  showSearch
                  optionFilterProp="label"
                />
              </Descriptions.Item>
              <Descriptions.Item label="样本池">{submittedStats ? `${submittedStats.sample_pool_count} 条` : "未同步"}</Descriptions.Item>
              <Descriptions.Item label="固定测试集">{testset ? `${testset.sample_count} 条` : "未固定"}</Descriptions.Item>
            </Descriptions>
          </Space>
        </Card>

        {loading ? <Spin /> : null}

        {currentStep === 0 ? (
          <Card title="步骤 1：获取测试样本">
            {selectedTask ? (
              <Space direction="vertical" size="large" style={{ width: "100%" }}>
                <Alert type="info" showIcon message="当前任务已提交样本集合" description="数据集展示名直接使用任务名称；样本池数量按当前任务实际可获取到的已提交样本数决定。" />
                <Descriptions bordered size="small" column={2}>
                  <Descriptions.Item label="任务名称">{selectedTask.raw_task_name || selectedTask.task_short_name || selectedTask.task_id}</Descriptions.Item>
                  <Descriptions.Item label="任务 ID">{selectedTask.task_id}</Descriptions.Item>
                  <Descriptions.Item label="样本池">{submittedStats ? `${submittedStats.sample_pool_count} 条` : "未同步"}</Descriptions.Item>
                  <Descriptions.Item label="固定测试集">{testset ? `${testset.sample_count} 条` : "未固定"}</Descriptions.Item>
                  <Descriptions.Item label="最近同步">{submittedStats?.last_synced_at || "-"}</Descriptions.Item>
                  <Descriptions.Item label="当前能力">{selectedDraft ? `${selectedDraft.status} / ${selectedDraft.version}` : "未创建"}</Descriptions.Item>
                </Descriptions>
                <Space wrap>
                  <Button type="primary" loading={syncLoading} onClick={() => void handleSyncSubmittedHistory(false)}>同步已提交样本</Button>
                  <Button loading={syncLoading} onClick={() => void handleSyncSubmittedHistory(true)}>强制重同步</Button>
                  <Select
                    style={{ width: 280 }}
                    value={syncConfig.account_id || undefined}
                    placeholder="样本来源账号（默认任务源）"
                    options={accounts.map((account) => ({
                      value: account.user_id,
                      label: `${account.display_name || account.custom_name || account.user_id} / ${account.user_id}`,
                    }))}
                    onChange={(value) => setSyncConfig((current) => ({ ...current, account_id: value }))}
                    allowClear
                    showSearch
                    optionFilterProp="label"
                  />
                  <Input
                    style={{ width: 150 }}
                    type="number"
                    addonBefore="NodeID"
                    value={syncConfig.node_id}
                    onChange={(event) => setSyncConfig((current) => ({ ...current, node_id: Number(event.target.value) || 1 }))}
                  />
                  <Select
                    style={{ width: 180 }}
                    value={syncConfig.sample_count}
                    options={[10, 20, 30].map((value) => ({ value, label: `固定测试集 ${value} 条` }))}
                    onChange={(value) => setSyncConfig((current) => ({ ...current, sample_count: value }))}
                  />
                  <Button loading={testsetLoading} onClick={() => void handleGenerateTestset(syncConfig.sample_count)}>生成固定测试集</Button>
                  <Button loading={testsetLoading} disabled={!generatedTestset} onClick={() => void handleSaveTestset()}>固定当前测试集</Button>
                  <Button type="primary" disabled={!testset} onClick={() => setCurrentStep(1)}>下一步：能力调教</Button>
                </Space>
                <Card title="固定测试集预览" size="small" className="workbench-card">
                  {testsetRows.length ? <Table size="small" columns={testsetColumns} dataSource={testsetRows} rowKey="uid" pagination={false} scroll={{ x: "max-content" }} /> : <Empty description="先同步已提交样本并生成固定测试集。" />}
                </Card>
                <Card title="已提交样本池预览" size="small" className="workbench-card">
                  {submittedSamples.length ? <Table size="small" columns={submittedColumns} dataSource={submittedSamples.slice(0, 10)} rowKey="uid" pagination={false} scroll={{ x: "max-content" }} /> : <Empty description="先同步已提交样本。" />}
                </Card>
              </Space>
            ) : (
              <Empty description="当前没有可选任务，先刷新任务目录。" />
            )}
          </Card>
        ) : null}

        {currentStep === 1 ? (
          <Card title="步骤 2：能力调教">
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Descriptions bordered size="small" column={4}>
                <Descriptions.Item label="当前任务">{selectedTask?.raw_task_name || selectedTask?.task_id || "-"}</Descriptions.Item>
                <Descriptions.Item label="固定测试集">{testset ? `${testset.sample_count} 条` : "未固定"}</Descriptions.Item>
                <Descriptions.Item label="能力草稿">{selectedDraft ? `${selectedDraft.status} / ${selectedDraft.version}` : "未创建"}</Descriptions.Item>
                <Descriptions.Item label="学习包">
                  <Select
                    className="workbench-learning-select"
                    style={{ width: 360, maxWidth: "100%" }}
                    value={selectedLearningPackageId || undefined}
                    placeholder={learningPackages.length ? "选择学习包" : "学习包：未上传"}
                    options={learningPackages.map((item) => ({
                      value: item.learning_package_id,
                      label: `${item.display_name}  ${learningPackageSourceLabel(item.source)}  ${learningPackageStateLabel(item)}${item.selected ? "  当前" : ""}`,
                    }))}
                    onChange={(value) => void handleSelectLearningPackage(value)}
                    loading={learningPackageSaving}
                    showSearch
                    optionFilterProp="label"
                  />
                </Descriptions.Item>
              </Descriptions>
              <Row gutter={[16, 16]} align="top" className="workbench-step2-row">
                <Col xs={24} xxl={4} xl={6}>
                  <Card title="AI 助手" size="small" className="workbench-card workbench-scroll-card">
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <div className="ai-chat-window">
                        {chatMessages.map((item, index) => (
                          <div key={`${item.role}-${index}`} className={`ai-chat-bubble ${item.role === "user" ? "user" : "assistant"}`}>
                            {item.content}
                          </div>
                        ))}
                      </div>
                      <Input.TextArea
                        rows={5}
                        value={chatInput}
                        onChange={(event) => setChatInput(event.target.value)}
                        onPressEnter={(event) => {
                          if (!event.shiftKey) {
                            event.preventDefault();
                            void handleSendTaskChat();
                          }
                        }}
                        placeholder="例如：根据当前 10 条测试集，帮我把 Prompt 改得更严格一些。"
                      />
                      <Space wrap>
                        <Button type="primary" loading={chatLoading} disabled={!selectedTaskId} onClick={() => void handleSendTaskChat()}>发送给任务内 AI</Button>
                        <Button loading={applySuggestionLoading} disabled={chatMessages.filter((item) => item.role === "assistant").length === 0} onClick={() => void handleApplyLastAssistantSuggestion()}>应用修改到草稿</Button>
                        <Button disabled={!draftUndoStack.length} onClick={() => handleUndoDraftSuggestion()}>撤销修改</Button>
                        <Button onClick={() => setChatMessages([{ role: "assistant", content: "聊天已清空；继续把规则、样例和判分要求发给我，我会整理成完整 Prompt 建议。" }])}>清空聊天</Button>
                      </Space>
                    </Space>
                  </Card>
                </Col>
                <Col xs={24} xxl={8} xl={9}>
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    <Card title="做题 Prompt" size="small" className="workbench-card">
                      {selectedDraft ? (
                        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                          <div>
                            <Typography.Text strong>完整 Prompt 草稿</Typography.Text>
                            <Input.TextArea rows={18} value={draftForm.system_ai_draft} onChange={(event) => setDraftForm((current) => ({ ...current, system_ai_draft: event.target.value }))} />
                          </div>
                          <Space wrap>
                            <Button type="primary" onClick={() => void handleSaveDraft()}>保存当前 Prompt</Button>
                            <Button onClick={() => setDraftForm({ system_ai_draft: selectedDraft.system_ai_draft })}>恢复当前已加载版本</Button>
                          </Space>
                        </Space>
                      ) : <Empty description="当前任务还没有能力草稿。" />}
                    </Card>
                    <Card title="Prompt 快照历史" size="small" className="workbench-card">
                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                        <Select
                          style={{ width: "100%" }}
                          value={selectedSnapshotId || undefined}
                          placeholder="选择一个历史快照"
                          options={promptSnapshots.map((item) => ({
                            value: item.snapshot_id,
                            label: `${item.snapshot_id} / ${item.created_at}`,
                          }))}
                          onChange={setSelectedSnapshotId}
                          showSearch
                          optionFilterProp="label"
                        />
                        <Space wrap>
                          <Button onClick={() => void handleCreatePromptSnapshot()} loading={snapshotLoading} disabled={!selectedDraft}>创建 Prompt 快照</Button>
                          <Button onClick={() => void handleRestorePromptSnapshot()} loading={snapshotLoading} disabled={!selectedSnapshotId}>恢复所选快照</Button>
                          <Button href="/tasks">任务操作台</Button>
                        </Space>
                        {snapshotDiffRows.length ? (
                          <div className="workbench-json-panel">
                            <pre className="pre-wrap">{JSON.stringify(snapshotDiffRows, null, 2)}</pre>
                          </div>
                        ) : (
                          <Typography.Text type="secondary">当前草稿与所选快照没有差异，或尚未选择快照。</Typography.Text>
                        )}
                      </Space>
                    </Card>
                  </Space>
                </Col>
                <Col xs={24} xxl={12} xl={9}>
                  <Card
                    title="10 题回放结果"
                    extra={<Button loading={replayLoading} disabled={!selectedTaskId || !testset} onClick={() => void handleRefreshReplay()}>重新回放</Button>}
                    size="small"
                    className="workbench-card workbench-scroll-card"
                  >
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      {replayReportMeta ? (
                        <Descriptions bordered size="small" column={1}>
                          <Descriptions.Item label="回放报告">{replayReportMeta.report_id}</Descriptions.Item>
                          <Descriptions.Item label="生成时间">{replayReportMeta.created_at}</Descriptions.Item>
                        </Descriptions>
                      ) : null}
                      {replayCards.length ? (
                        <div className="workbench-replay-grid">
                          {replayCards.map((card: TaskAbilityReplayCard) => (
                            <Card
                              key={card.uid}
                              size="small"
                              className={`workbench-replay-card ${selectedReplayUid === card.uid ? "is-selected" : ""}`}
                              onClick={() => void handleSelectReplaySample(card.uid)}
                            >
                              <Space direction="vertical" size="small" style={{ width: "100%" }}>
                                <Typography.Text strong>{card.display_title}</Typography.Text>
                                <Tag color={card.status === "success" ? "green" : card.status === "error" ? "red" : "gold"}>
                                  {card.status === "success" ? "已回放" : card.status === "error" ? "回放失败" : card.status}
                                </Tag>
                                <div className="workbench-replay-images">
                                  <div>
                                    <Typography.Text type="secondary">原图</Typography.Text>
                                    {card.images.original?.available && card.images.original.url ? (
                                      <Image src={card.images.original.url} alt="原图" className="workbench-replay-image" />
                                    ) : (
                                      <div className="workbench-replay-missing">原图缺失</div>
                                    )}
                                  </div>
                                  <div>
                                    <Typography.Text type="secondary">AI图</Typography.Text>
                                    {card.images.ai?.available && card.images.ai.url ? (
                                      <Image src={card.images.ai.url} alt="AI图" className="workbench-replay-image" />
                                    ) : (
                                      <div className="workbench-replay-missing">AI图缺失</div>
                                    )}
                                  </div>
                                </div>
                                <Typography.Text>评分：{card.score || "-"}</Typography.Text>
                                <Typography.Paragraph className="workbench-replay-reason">{card.reason || "无理由"}</Typography.Paragraph>
                                {card.error_message ? <Typography.Text type="danger">{card.error_message}</Typography.Text> : null}
                              </Space>
                            </Card>
                          ))}
                        </div>
                      ) : (
                        <Empty description="当前还没有回放结果；先固定测试集，再点击重新回放。" />
                      )}
                    </Space>
                  </Card>
                </Col>
              </Row>
              <Space wrap>
                <Button type="primary" disabled={!testset} onClick={() => setCurrentStep(2)}>下一步：Live 暂存验证</Button>
              </Space>
            </Space>
          </Card>
        ) : null}

        {currentStep === 2 ? (
          <Card title="步骤 3：Live 暂存验证">
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Alert type="warning" showIcon message="纯 HTTP 端到端不提交暂存测试" description="这里默认回读最近一次正式 Live 暂存报告。页面刷新后仍能看到最近结果，并继续明确显示已暂存/未正式提交。" />
              <Descriptions bordered size="small" column={2}>
                <Descriptions.Item label="当前任务">{selectedTask?.raw_task_name || selectedTask?.task_id || "-"}</Descriptions.Item>
                <Descriptions.Item label="执行边界">纯 HTTP 端到端不提交暂存测试</Descriptions.Item>
                <Descriptions.Item label="验证账号">
                  <Select
                    style={{ width: 320 }}
                    value={liveAccountId || undefined}
                    options={liveAccountOptions}
                    onChange={setLiveAccountId}
                    showSearch
                    optionFilterProp="label"
                  />
                </Descriptions.Item>
                <Descriptions.Item label="最近报告">{liveTestReportId || "暂无"}</Descriptions.Item>
                <Descriptions.Item label="已暂存">{liveTestResult ? (liveTestResult.saved_to_task_ui ? "是" : "否") : "未执行"}</Descriptions.Item>
                <Descriptions.Item label="已正式提交">{liveTestResult ? (liveTestResult.submits_remote ? "是" : "否") : "否"}</Descriptions.Item>
              </Descriptions>
              {liveTestResult ? (
                <Card title="最近一次 Live 暂存验证结果" size="small" className="workbench-card">
                  <Tabs
                    items={[
                      {
                        key: "output",
                        label: "AI 输出预览",
                        children: <div className="workbench-json-panel"><pre className="pre-wrap">{JSON.stringify(liveTestResult.saved_answer || liveTestResult.answer_preview, null, 2)}</pre></div>,
                      },
                      {
                        key: "request",
                        label: "请求 / Payload",
                        children: <div className="workbench-json-panel"><pre className="pre-wrap">{JSON.stringify(liveTestResult.temp_draft_payload_preview || {}, null, 2)}</pre></div>,
                      },
                      {
                        key: "response",
                        label: "响应",
                        children: <div className="workbench-json-panel"><pre className="pre-wrap">{JSON.stringify(liveTestResult.temp_draft_result || {}, null, 2)}</pre></div>,
                      },
                      {
                        key: "validation",
                        label: "字段校验",
                        children: (
                          <Descriptions bordered size="small" column={1}>
                            <Descriptions.Item label="阶段">{liveTestResult.stage}</Descriptions.Item>
                            <Descriptions.Item label="审核状态">{liveTestResult.review_status}</Descriptions.Item>
                            <Descriptions.Item label="题目ID">{String(liveTestResult.question_context?.item_id || "-")}</Descriptions.Item>
                            <Descriptions.Item label="请求边界">{liveTestResult.writes_remote ? "已写入暂存接口" : "未写入"} / 正式提交：{liveTestResult.submits_remote ? "是" : "否"}</Descriptions.Item>
                            <Descriptions.Item label="结果说明">{liveTestResult.ui_review_hint || liveTestResult.message}</Descriptions.Item>
                          </Descriptions>
                        ),
                      },
                    ]}
                  />
                </Card>
              ) : (
                <Empty description="当前还没有 Live 暂存验证记录。" />
              )}
              <Space wrap>
                <Button type="primary" loading={liveTestLoading} onClick={() => void handleRunLiveHttpTest()}>执行 Live 暂存验证</Button>
                <Button onClick={() => void loadTaskArtifacts(selectedTaskId)} disabled={!selectedTaskId}>读取最近一次 Live 结果</Button>
                <Button href="/tasks">进入任务操作台查看旧流程</Button>
                <Button onClick={() => setCurrentStep(3)}>下一步：批准并运行</Button>
              </Space>
            </Space>
          </Card>
        ) : null}

        {currentStep === 3 ? (
          <Card title="步骤 4：批准并运行">
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Alert type="success" showIcon message="人工批准后进入运行" description={runGate?.next_step || "试运行后进入生产运行采用人工拍板，不额外增加自动护栏。"} />
              <Descriptions bordered size="small" column={2}>
                <Descriptions.Item label="当前任务">{selectedTask?.raw_task_name || selectedTask?.task_id || "-"}</Descriptions.Item>
                <Descriptions.Item label="能力版本">{runGate?.ability_version || selectedDraft?.version || "-"}</Descriptions.Item>
                <Descriptions.Item label="当前状态">{runGate?.flow_stage || selectedDraft?.flow_stage || "-"}</Descriptions.Item>
                <Descriptions.Item label="人工审核状态">{runGate?.review_status || "-"}</Descriptions.Item>
                <Descriptions.Item label="固定测试集">{testset ? `${testset.sample_count} 条` : "未固定"}</Descriptions.Item>
                <Descriptions.Item label="最近 Live 报告">{liveTestReportId || "暂无"}</Descriptions.Item>
                <Descriptions.Item label="试运行记录">{lastTrialRun?.run_id || "暂无"}</Descriptions.Item>
                <Descriptions.Item label="生产运行记录">{lastProductionRun?.run_id || "暂无"}</Descriptions.Item>
              </Descriptions>
              <Row gutter={[16, 16]}>
                <Col xs={24} xl={12}>
                  <Card title="运行账号与自检" size="small">
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <Select
                        mode="multiple"
                        style={{ width: "100%" }}
                        value={runAccountIds}
                        options={runAccountOptions}
                        placeholder="选择要进入试运行/生产运行的账号"
                        onChange={setRunAccountIds}
                        optionFilterProp="label"
                        showSearch
                      />
                      <Space wrap>
                        <Button type="primary" loading={approveLoading} disabled={!runGate?.can_approve} onClick={() => void handleApproveAbilityVersion()}>批准当前能力版本</Button>
                        <Button loading={preflightLoading} onClick={() => void handleRunPreflight()} disabled={!selectedTaskId || !runAccountIds.length}>启动前自检</Button>
                        <Button loading={trialLoading} disabled={!runGate?.can_start_trial || !runAccountIds.length} onClick={() => void handleStartTrialRun()}>启动试运行</Button>
                        <Button loading={productionLoading} disabled={!runGate?.can_start_production || !runAccountIds.length} onClick={() => void handleStartProductionRun()}>启动生产运行</Button>
                        <Button loading={runControlLoading} disabled={!managedRun?.run_id} onClick={() => void handlePauseManagedRun()}>暂停运行</Button>
                        <Button danger loading={runControlLoading} disabled={!managedRun?.run_id} onClick={() => void handleStopManagedRun()}>停止运行</Button>
                      </Space>
                      {managedRun ? (
                        <Descriptions bordered size="small" column={1}>
                          <Descriptions.Item label="当前运行ID">{managedRun.run_id}</Descriptions.Item>
                          <Descriptions.Item label="当前运行状态">{managedRun.status}</Descriptions.Item>
                          <Descriptions.Item label="Worker 状态">{managedWorkerStatus ? `${managedWorkerStatus.active ? "active" : "stopped"} / cycle=${managedWorkerStatus.cycle_count}` : "暂无"}</Descriptions.Item>
                        </Descriptions>
                      ) : null}
                    </Space>
                  </Card>
                </Col>
                <Col xs={24} xl={12}>
                  <Card title="运行参数语义" size="small">
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <Select
                        value={runConfig.mode}
                        options={[
                          { value: "safe", label: "慢速安全" },
                          { value: "normal", label: "普通生产" },
                          { value: "aggressive", label: "激进抢题" },
                        ]}
                        onChange={(value) => setRunConfig((current) => ({ ...current, mode: value }))}
                      />
                      <Input type="number" addonBefore="提交速率上限(题/分钟)" value={runConfig.rate_limit_per_minute} onChange={(event) => setRunConfig((current) => ({ ...current, rate_limit_per_minute: Number(event.target.value) || 1 }))} />
                      <Input type="number" addonBefore="试运行总提交上限/账号" value={runConfig.trial_max_items_per_account} onChange={(event) => setRunConfig((current) => ({ ...current, trial_max_items_per_account: Number(event.target.value) || 1 }))} />
                      <Input type="number" addonBefore="生产运行总提交上限/账号" value={runConfig.production_max_items_per_account} onChange={(event) => setRunConfig((current) => ({ ...current, production_max_items_per_account: Number(event.target.value) || 1 }))} />
                      <Input type="number" addonBefore="连续失败阈值" value={runConfig.consecutive_fail_threshold} onChange={(event) => setRunConfig((current) => ({ ...current, consecutive_fail_threshold: Number(event.target.value) || 1 }))} />
                      <Space wrap>
                        <Button loading={runConfigSaving} disabled={!selectedTaskId} onClick={() => void handleSaveRunConfig()}>保存运行配置</Button>
                        <Typography.Text type="secondary">当前配置会持久化到任务级状态，并在启动试运行/生产运行时一起下发到后端执行器。</Typography.Text>
                      </Space>
                    </Space>
                  </Card>
                </Col>
              </Row>
              {preflightResult ? (
                <Card title="启动前自检结果" size="small">
                  <Descriptions bordered size="small" column={1}>
                    <Descriptions.Item label="状态">{preflightResult.status}</Descriptions.Item>
                    <Descriptions.Item label="可启动">{preflightResult.can_start ? "是" : "否"}</Descriptions.Item>
                    <Descriptions.Item label="说明">{preflightResult.message}</Descriptions.Item>
                    <Descriptions.Item label="下一步">{preflightResult.next_step}</Descriptions.Item>
                    <Descriptions.Item label="检查项">
                      <pre className="pre-wrap">{JSON.stringify(preflightResult.checks, null, 2)}</pre>
                    </Descriptions.Item>
                  </Descriptions>
                </Card>
              ) : null}
            </Space>
          </Card>
        ) : null}
      </Space>
    </div>
  );
}
