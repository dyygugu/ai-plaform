import { Alert, Button, Card, Col, Descriptions, Input, List, Row, Space, Statistic, Table, Tabs, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import {
  approveAiConfirmation,
  captureScoreLoopCase,
  draftScoreLoopCase,
  fetchAiConfig,
  fetchAiConfigCheck,
  fetchAiConfirmations,
  fetchAiQueue,
  fetchScoreLoopCases,
  fetchScoreLoopSummary,
  rejectAiConfirmation,
  reviewIncidentAi,
  reviewScoreLoopCase,
  sendAiChat,
  updateAiConfig,
  updateScoreLoopAutoSubmit,
  type AiActionConfirmationItem,
  type AiActionConfirmationSummary,
  type AiConfigCheckResponse,
  type AiChatMessage,
  type AiIncidentAction,
  type AiIncidentReviewResponse,
  type AiJobItem,
  type AiQueueSummary,
  type AiRuntimeConfig,
  type ScoreLoopCaseItem,
  type ScoreLoopCaseListResponse,
  type ScoreLoopSummaryResponse,
} from "../api/client";

const statusColor: Record<string, string> = {
  mock_completed: "green",
  provider_gated: "gold",
  failed: "red",
  planned: "blue",
  passed: "green",
  warning: "gold",
  blocked: "red",
  dry_run: "blue",
  auto_executed: "green",
  requires_confirmation: "red",
  pending: "red",
  approved: "green",
  rejected: "gray",
  expired: "orange",
  captured: "blue",
  unsupported_paused: "orange",
  draft_ready: "gold",
  manual_approved: "green",
  manual_rejected: "gray",
  submit_confirmation_required: "red",
  manual_confirmation: "gold",
  confirmation_queued: "gold",
  auto_submit: "green",
  auto_submit_ready: "green",
  account_cookie_missing: "red",
  no_current_item: "orange",
  first_item_review: "gold",
  waiting_first_confirm: "gold",
  waiting_first_gate: "gold",
  waiting_review: "gold",
  first_item_approved: "gold",
  waiting_first_submit: "gold",
  approved_pending_submit: "gold",
  auto_parallel: "green",
  running_auto: "green",
  waiting_operation_claim: "orange",
  operation_claim_needed: "orange",
  stopped: "gray",
};

const columns: ColumnsType<AiJobItem> = [
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "blue"}>{value}</Tag> },
  { title: "摘要", dataIndex: "prompt_summary", key: "prompt_summary" },
  { title: "结果", dataIndex: "result_summary", key: "result_summary" },
  { title: "总耗时ms", dataIndex: "total_ms", key: "total_ms" },
  { title: "trace_id", dataIndex: "trace_id", key: "trace_id" },
];


type ProviderForm = {
  base_url: string;
  api_key: string;
  model: string;
  timeout_seconds: number;
  pre_prompt: string;
  skills_text: string;
  md_files_text: string;
};

const defaultProviderForm: ProviderForm = {
  base_url: "",
  api_key: "",
  model: "gpt-4.1-mini",
  timeout_seconds: 30,
  pre_prompt: "",
  skills_text: "",
  md_files_text: "",
};

const splitLines = (value: string) => value.split(/[\n,，]+/).map((item) => item.trim()).filter(Boolean);
const BON8_TASK_ID = "7637771731901861641";

const providerPayload = (form: ProviderForm) => ({
  base_url: form.base_url,
  api_key: form.api_key,
  model: form.model,
  timeout_seconds: form.timeout_seconds,
  pre_prompt: form.pre_prompt,
  skills: splitLines(form.skills_text),
  md_files: splitLines(form.md_files_text),
});

const actionColumns: ColumnsType<AiIncidentAction> = [
  { title: "动作", dataIndex: "title", key: "title" },
  { title: "风险", dataIndex: "risk_level", key: "risk_level", render: (value: string) => <Tag color={value === "high" ? "red" : value === "medium" ? "gold" : "green"}>{value}</Tag> },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "blue"}>{value}</Tag> },
  { title: "需确认", dataIndex: "requires_confirmation", key: "requires_confirmation", render: (value: boolean) => value ? <Tag color="red">需要</Tag> : <Tag color="green">不需要</Tag> },
  { title: "说明", dataIndex: "message", key: "message" },
  { title: "回滚", dataIndex: "rollback_hint", key: "rollback_hint" },
];

export function AiPage() {
  const [queue, setQueue] = useState<AiQueueSummary | null>(null);
  const [confirmations, setConfirmations] = useState<AiActionConfirmationSummary | null>(null);
  const [scoreSummary, setScoreSummary] = useState<ScoreLoopSummaryResponse | null>(null);
  const [scoreCases, setScoreCases] = useState<ScoreLoopCaseListResponse | null>(null);
  const [review, setReview] = useState<AiIncidentReviewResponse | null>(null);
  const [aiConfig, setAiConfig] = useState<AiRuntimeConfig | null>(null);
  const [aiConfigCheck, setAiConfigCheck] = useState<AiConfigCheckResponse | null>(null);
  const [configForm, setConfigForm] = useState({ system_ai: defaultProviderForm, task_ai: defaultProviderForm, task_ai_managed_by_system_ai: true });
  const [chatInput, setChatInput] = useState("");
  const [chatMessages, setChatMessages] = useState<AiChatMessage[]>([
    { role: "assistant", content: "我是系统处理 AI，负责运维、排障、配置和系统处置；做题答案会交给做题 AI 只在做题链路调用。" },
  ]);
  const [loading, setLoading] = useState(false);
  const [configLoading, setConfigLoading] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [decisionLoadingId, setDecisionLoadingId] = useState<number | null>(null);
  const [scoreLoadingKey, setScoreLoadingKey] = useState<string | null>(null);

  const loadAll = async () => {
    const [queueResult, confirmationResult, scoreSummaryResult, scoreCasesResult, configResult] = await Promise.all([
      fetchAiQueue(),
      fetchAiConfirmations(),
      fetchScoreLoopSummary(),
      fetchScoreLoopCases(),
      fetchAiConfig().catch(() => null),
    ]);
    setQueue(queueResult);
    setConfirmations(confirmationResult);
    setScoreSummary(scoreSummaryResult);
    setScoreCases(scoreCasesResult);
    if (configResult) {
      setAiConfig(configResult);
      setConfigForm((current) => ({
        ...current,
        task_ai_managed_by_system_ai: configResult.task_ai_managed_by_system_ai,
        system_ai: {
          ...current.system_ai,
          base_url: configResult.system_ai.base_url,
          model: configResult.system_ai.model,
          timeout_seconds: configResult.system_ai.timeout_seconds,
          pre_prompt: configResult.system_ai.pre_prompt,
          skills_text: configResult.system_ai.skills.join("\n"),
          md_files_text: configResult.system_ai.md_files.join("\n"),
        },
        task_ai: {
          ...current.task_ai,
          base_url: configResult.task_ai.base_url,
          model: configResult.task_ai.model,
          timeout_seconds: configResult.task_ai.timeout_seconds,
          pre_prompt: configResult.task_ai.pre_prompt,
          skills_text: configResult.task_ai.skills.join("\n"),
          md_files_text: configResult.task_ai.md_files.join("\n"),
        },
      }));
    }
    setAiConfigCheck(await fetchAiConfigCheck().catch(() => null));
  };

  useEffect(() => { void loadAll(); }, []);

  const runIncidentReview = async () => {
    setLoading(true);
    try {
      const result = await reviewIncidentAi();
      setReview(result);
      await loadAll();
      message.success("系统 AI 运维评估完成，高危动作已进入确认队列");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "事故 AI 调用失败");
    } finally {
      setLoading(false);
    }
  };

  const saveAiConfig = async () => {
    setConfigLoading(true);
    try {
      const result = await updateAiConfig({
        system_ai: providerPayload(configForm.system_ai),
        task_ai: providerPayload(configForm.task_ai),
        task_ai_managed_by_system_ai: configForm.task_ai_managed_by_system_ai,
      });
      setAiConfig(result);
      setConfigForm((current) => ({
        ...current,
        system_ai: { ...current.system_ai, api_key: "" },
        task_ai: { ...current.task_ai, api_key: "" },
      }));
      setAiConfigCheck(await fetchAiConfigCheck());
      message.success(result.message);
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "保存 AI 配置失败");
    } finally {
      setConfigLoading(false);
    }
  };

  const sendChat = async () => {
    const content = chatInput.trim();
    if (!content) return;
    const nextHistory = [...chatMessages, { role: "user", content }];
    setChatMessages(nextHistory);
    setChatInput("");
    setChatLoading(true);
    try {
      const result = await sendAiChat({ message: content, history: nextHistory.slice(-8), use_provider: true });
      setChatMessages([...nextHistory, { role: "assistant", content: `${result.answer}\n\n调用状态：${result.provider_status}；追踪ID：${result.trace_id}` }]);
      await loadAll();
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "系统 AI 聊天失败");
    } finally {
      setChatLoading(false);
    }
  };

  const approveConfirmation = async (item: AiActionConfirmationItem) => {
    const input = window.prompt(`请输入确认短语 ${item.confirm_phrase}。批准只记录授权和审计，不会自动执行高危动作。`);
    if (input === null) return;
    setDecisionLoadingId(item.id);
    try {
      await approveAiConfirmation(item.id, { operator: "admin", note: "前端人工确认高危动作", confirm_text: input, write_audit: true });
      await loadAll();
      message.success("高危动作已确认，已写入审计");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "确认失败");
    } finally {
      setDecisionLoadingId(null);
    }
  };

  const rejectConfirmation = async (item: AiActionConfirmationItem) => {
    const note = window.prompt("请输入驳回原因", "人工驳回高危动作");
    if (note === null) return;
    setDecisionLoadingId(item.id);
    try {
      await rejectAiConfirmation(item.id, { operator: "admin", note, write_audit: true });
      await loadAll();
      message.success("高危动作已驳回，已写入审计");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "驳回失败");
    } finally {
      setDecisionLoadingId(null);
    }
  };

  const captureScoreCase = async () => {
    const question = window.prompt("输入脱敏题面文本", "图片/页面美观度评分：请根据整体视觉质量选择 1-5 分。");
    if (!question) return;
    const choicesRaw = window.prompt("输入候选项，用逗号分隔", "1,2,3,4,5") ?? "";
    setScoreLoadingKey("capture");
    try {
      await captureScoreLoopCase({ question_text: question, choices: choicesRaw.split(",").map((item) => item.trim()).filter(Boolean), write_audit: true });
      await loadAll();
      message.success("题面已采集，下一步生成 AI 草稿");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "题面采集失败");
    } finally {
      setScoreLoadingKey(null);
    }
  };

  const draftScoreCase = async (item: ScoreLoopCaseItem) => {
    setScoreLoadingKey(`draft-${item.id}`);
    try {
      await draftScoreLoopCase(item.id);
      await loadAll();
      message.success("AI 草稿已生成，等待人工确认");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "生成草稿失败");
    } finally {
      setScoreLoadingKey(null);
    }
  };

  const approveScoreCase = async (item: ScoreLoopCaseItem, requestSubmit: boolean) => {
    const answer = window.prompt("确认最终答案", item.ai_answer || item.final_answer || "");
    if (!answer) return;
    setScoreLoadingKey(`review-${item.id}`);
    try {
      await reviewScoreLoopCase(item.id, { decision: "approve", final_answer: answer, note: "前端人工确认评分结果", request_submit: requestSubmit, write_audit: true });
      await loadAll();
      message.success(requestSubmit ? "已进入真实提交确认队列" : "评分结果已人工确认");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "人工确认失败");
    } finally {
      setScoreLoadingKey(null);
    }
  };

  const rejectScoreCase = async (item: ScoreLoopCaseItem) => {
    const note = window.prompt("请输入驳回原因", "人工驳回评分草稿");
    if (note === null) return;
    setScoreLoadingKey(`review-${item.id}`);
    try {
      await reviewScoreLoopCase(item.id, { decision: "reject", note, request_submit: false, write_audit: true });
      await loadAll();
      message.success("评分草稿已驳回");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "驳回评分失败");
    } finally {
      setScoreLoadingKey(null);
    }
  };

  const toggleAutoSubmit = async (enabled: boolean) => {
    setScoreLoadingKey("auto-submit");
    try {
      const result = await updateScoreLoopAutoSubmit({ enabled, force_confirmed: false, reason: "frontend gate toggle" });
      await loadAll();
      message.success(result.gate?.auto_submit_enabled ? "自动提交闸门已开启；真实提交仍需确认" : "自动提交闸门未开启或已关闭");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "更新自动提交闸门失败");
    } finally {
      setScoreLoadingKey(null);
    }
  };

  const updateProviderForm = (key: "system_ai" | "task_ai", patch: Partial<ProviderForm>) => {
    setConfigForm((current) => ({ ...current, [key]: { ...current[key], ...patch } }));
  };

  const confirmationColumns: ColumnsType<AiActionConfirmationItem> = [
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "blue"}>{value}</Tag> },
    { title: "动作", dataIndex: "title", key: "title" },
    { title: "风险", dataIndex: "risk_level", key: "risk_level", render: (value: string) => <Tag color={value === "high" ? "red" : "gold"}>{value}</Tag> },
    { title: "确认短语", dataIndex: "confirm_phrase", key: "confirm_phrase" },
    { title: "说明", dataIndex: "message", key: "message" },
    { title: "下一步", dataIndex: "next_step", key: "next_step" },
    {
      title: "操作",
      key: "actions",
      render: (_, item) => item.status === "pending" ? (
        <Space>
          <Button danger size="small" loading={decisionLoadingId === item.id} onClick={() => void approveConfirmation(item)}>确认授权</Button>
          <Button size="small" loading={decisionLoadingId === item.id} onClick={() => void rejectConfirmation(item)}>驳回</Button>
        </Space>
      ) : <Typography.Text type="secondary">{item.reviewed_by ?? "已处理"}</Typography.Text>,
    },
  ];

  const scoreColumns: ColumnsType<ScoreLoopCaseItem> = [
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "blue"}>{value}</Tag> },
    { title: "题型", dataIndex: "task_type_name", key: "task_type_name" },
    { title: "题面摘要", dataIndex: "question_text", key: "question_text", render: (value: string) => value.slice(0, 80) },
    { title: "AI 草稿", dataIndex: "ai_answer", key: "ai_answer" },
    { title: "最终答案", dataIndex: "final_answer", key: "final_answer" },
    { title: "提交确认ID", dataIndex: "submit_confirmation_id", key: "submit_confirmation_id", render: (value: number | null) => value ?? "-" },
    { title: "下一步", dataIndex: "next_step", key: "next_step" },
    {
      title: "操作",
      key: "actions",
      render: (_, item) => (
        <Space wrap>
          <Button size="small" disabled={!['captured'].includes(item.status)} loading={scoreLoadingKey === `draft-${item.id}`} onClick={() => void draftScoreCase(item)}>调用做题 AI 草稿</Button>
          <Button size="small" disabled={!['draft_ready', 'manual_approved'].includes(item.status)} loading={scoreLoadingKey === `review-${item.id}`} onClick={() => void approveScoreCase(item, false)}>人工确认</Button>
          <Button danger size="small" disabled={!['draft_ready', 'manual_approved'].includes(item.status)} loading={scoreLoadingKey === `review-${item.id}`} onClick={() => void approveScoreCase(item, true)}>请求真实提交</Button>
          <Button size="small" disabled={!['captured', 'draft_ready'].includes(item.status)} loading={scoreLoadingKey === `review-${item.id}`} onClick={() => void rejectScoreCase(item)}>驳回</Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="page-stack">
      <Space align="start" className="page-heading">
        <div>
          <Typography.Title level={2} style={{ marginBottom: 4 }}>AI 中心</Typography.Title>
          <Typography.Text type="secondary">系统 AI 管运维和能力制作；AI 做题按题型配置，bon8 只是其中一个题型能力。</Typography.Text>
        </div>
        <Space>
          <Button onClick={() => void loadAll()}>刷新队列</Button>
          <Button type="primary" loading={loading} onClick={runIncidentReview}>运行系统 AI 运维评估</Button>
        </Space>
      </Space>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}><Card><Statistic title="系统 AI" value={aiConfig?.system_ai.api_key_configured ? "已配置" : "未配置"} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="AI 做题" value={aiConfig?.task_ai.api_key_configured ? "已配置" : "未配置"} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="可生产题型" value={2} suffix="个" /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="待确认高危动作" value={confirmations?.pending ?? 0} valueStyle={{ color: (confirmations?.pending ?? 0) > 0 ? "#cf1322" : undefined }} /></Card></Col>
      </Row>

      <Tabs
        defaultActiveKey="system-ai"
        items={[
          {
            key: "system-ai",
            label: "系统 AI",
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Alert
                  type="warning"
                  showIcon
                  message="高危动作确认闸门"
                  description="真实提交、删除/覆盖数据、改密钥、切正式域名、批量停用、改安全策略、清日志/备份等动作必须先进入确认队列；确认按钮只记录人工授权和审计，不自动执行破坏性动作。"
                />
                <Row gutter={[16, 16]}>
                  <Col xs={24} xl={14}>
                    <Card title="系统 AI 聊天">
                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                        <Alert type="info" showIcon message="系统处理 AI" description="日常不需要看运维页面；出问题直接问这里。系统 AI 可读取生产上下文，辅助排障、配置、制作 AI 做题能力和高危动作确认。" />
                        <div className="ai-chat-window">
                          {chatMessages.map((item, index) => <div key={`${item.role}-${index}`} className={`ai-chat-bubble ${item.role === "user" ? "user" : "assistant"}`}>{item.content}</div>)}
                        </div>
                        <Input.TextArea rows={3} value={chatInput} onChange={(event) => setChatInput(event.target.value)} onPressEnter={(event) => { if (!event.shiftKey) { event.preventDefault(); void sendChat(); } }} placeholder="问：为什么没题？哪个账号异常？要怎么排障或配置？" />
                        <Space>
                          <Button type="primary" loading={chatLoading} onClick={() => void sendChat()}>发送给系统 AI</Button>
                          <Button onClick={() => setChatMessages([{ role: "assistant", content: "聊天已清空；我是系统处理 AI，仍会按生产上下文和护栏回答。" }])}>清空聊天</Button>
                        </Space>
                      </Space>
                    </Card>
                  </Col>
                  <Col xs={24} xl={10}>
                    <Card title="系统 AI 配置与自检">
                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                        <Alert type="success" showIcon message="系统 AI 用途" description="负责运维、配置、高危确认，以及未来制作和维护 AI 做题题型能力。" />
                        <Descriptions bordered size="small" column={1}>
                          <Descriptions.Item label="系统 AI">{aiConfig?.system_ai.api_key_configured ? <Tag color="green">已配置密钥</Tag> : <Tag color="gold">未配置密钥</Tag>}</Descriptions.Item>
                          <Descriptions.Item label="系统聊天">{aiConfigCheck?.ready_for_system_chat ? <Tag color="green">可用</Tag> : <Tag color="gold">本地护栏兜底</Tag>}</Descriptions.Item>
                          <Descriptions.Item label="说明">{aiConfig?.message ?? "填写系统 AI 的 Base URL、API Key 和模型名"}</Descriptions.Item>
                        </Descriptions>
                        {aiConfigCheck ? (
                          <Alert
                            type={aiConfigCheck.status === "passed" ? "success" : aiConfigCheck.status === "blocked" ? "error" : "warning"}
                            showIcon
                            message={`AI 配置自检：${aiConfigCheck.status}`}
                            description={aiConfigCheck.message}
                          />
                        ) : null}
                        <Card size="small" title="系统处理 AI（最高权限）">
                          <Space direction="vertical" size="small" style={{ width: "100%" }}>
                            <Typography.Text type="secondary">用于内置聊天、运维排障、系统配置、高危动作确认和做题 AI 前置配置管理。</Typography.Text>
                            <Input value={configForm.system_ai.base_url} onChange={(event) => updateProviderForm("system_ai", { base_url: event.target.value })} placeholder="系统 AI Base URL，例如 https://api.openai.com/v1" />
                            <Input.Password value={configForm.system_ai.api_key} onChange={(event) => updateProviderForm("system_ai", { api_key: event.target.value })} placeholder={aiConfig?.system_ai.api_key_configured ? "留空则保留系统 AI API Key" : "系统 AI API Key"} />
                            <Input value={configForm.system_ai.model} onChange={(event) => updateProviderForm("system_ai", { model: event.target.value })} placeholder="系统 AI 模型名" />
                            <Input type="number" value={configForm.system_ai.timeout_seconds} onChange={(event) => updateProviderForm("system_ai", { timeout_seconds: Number(event.target.value) || 30 })} placeholder="超时时间秒" />
                            <Input.TextArea rows={3} value={configForm.system_ai.pre_prompt} onChange={(event) => updateProviderForm("system_ai", { pre_prompt: event.target.value })} placeholder="系统 AI 额外前置提示词，可留空" />
                          </Space>
                        </Card>
                        <Space wrap>
                          <Button type="primary" loading={configLoading} onClick={() => void saveAiConfig()}>保存双 AI 配置</Button>
                          <Button onClick={async () => setAiConfigCheck(await fetchAiConfigCheck())}>重新自检</Button>
                        </Space>
                      </Space>
                    </Card>
                  </Col>
                </Row>
                <Card title="高危动作确认队列">
                  <Table columns={confirmationColumns} dataSource={confirmations?.items ?? []} rowKey="id" pagination={{ pageSize: 5 }} />
                </Card>
                <Card title="事故 AI 最近评估">
                  {review ? (
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <Descriptions bordered size="small" column={3}>
                        <Descriptions.Item label="状态"><Tag color={statusColor[review.status] ?? "blue"}>{review.status}</Tag></Descriptions.Item>
                        <Descriptions.Item label="Provider">{review.provider_status}</Descriptions.Item>
                        <Descriptions.Item label="权限模型">{review.permission_model}</Descriptions.Item>
                        <Descriptions.Item label="开放事故">{review.incident_count}</Descriptions.Item>
                        <Descriptions.Item label="动作数">{review.action_count}</Descriptions.Item>
                        <Descriptions.Item label="需确认">{review.confirmation_required_count}</Descriptions.Item>
                        <Descriptions.Item label="已入确认队列">{review.confirmation_request_count}</Descriptions.Item>
                        <Descriptions.Item label="确认项ID">{review.confirmation_ids.join(", ") || "无"}</Descriptions.Item>
                        <Descriptions.Item label="报告" span={3}>{review.report_path ?? "未生成"}</Descriptions.Item>
                        <Descriptions.Item label="护栏" span={3}>{review.guardrail_summary}</Descriptions.Item>
                        <Descriptions.Item label="前置上下文" span={3}>{String(review.context_summary.operator_context_file ?? "app/prompts/incident_ai_operator.md")}</Descriptions.Item>
                      </Descriptions>
                      <Card size="small" title="飞书通知预览"><pre className="pre-wrap">{review.feishu_notification_preview}</pre></Card>
                      <Table columns={actionColumns} dataSource={review.actions} rowKey="key" pagination={false} />
                    </Space>
                  ) : (
                    <List
                      dataSource={[
                        "点击运行后，AI 会读取告警、异常、账号、Worker 和最近审计日志。",
                        "Provider 调用前会加载 app/prompts/incident_ai_operator.md，先恢复项目功能地图、职责边界和执行顺序。",
                        "未配置 provider 时走本地护栏策略，仍会写审计和报告。",
                        "高危动作只生成确认项并进入确认队列，不会绕过自动提交、密钥、域名和删除护栏。",
                      ]}
                      renderItem={(item) => <List.Item>{item}</List.Item>}
                    />
                  )}
                </Card>
              </Space>
            ),
          },
          {
            key: "task-ai-factory",
            label: "AI 配置健康",
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Alert
                  type="info"
                  showIcon
                  message="具体任务启动已迁移到任务操作台"
                  description="AI 中心只保留做题 AI 配置和模型健康；bon8、科研图等任务的一键启动、账号选择、停止和状态查看都在对应任务操作台执行。"
                />
                <Card title="做题 AI 健康">
                  <Descriptions bordered size="small" column={2}>
                    <Descriptions.Item label="做题 AI">{aiConfig?.task_ai.api_key_configured ? <Tag color="green">已配置</Tag> : <Tag color="gold">未配置</Tag>}</Descriptions.Item>
                    <Descriptions.Item label="模型">{aiConfig?.task_ai.model || "-"}</Descriptions.Item>
                    <Descriptions.Item label="自检">{aiConfigCheck?.ready_for_task_draft ? <Tag color="green">可用</Tag> : <Tag color="gold">待检查</Tag>}</Descriptions.Item>
                    <Descriptions.Item label="系统托管">{aiConfig?.task_ai_managed_by_system_ai ? <Tag color="green">是</Tag> : <Tag color="red">否</Tag>}</Descriptions.Item>
                  </Descriptions>
                </Card>
              </Space>
            ),
          },
          {
            key: "task-type-config",
            label: "题型能力配置",
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Alert type="success" showIcon message="AI 做题配置入口" description="接口和密钥只是底座；真正可扩展的是按题型配置提示词、输出格式、字段映射、护栏、账号范围和执行模式。" />
                <Row gutter={[16, 16]}>
                  <Col xs={24} xl={10}>
                    <Card title="做题 AI（仅做题调用）">
                      <Space direction="vertical" size="small" style={{ width: "100%" }}>
                        <Descriptions bordered size="small" column={1}>
                          <Descriptions.Item label="做题 AI">{aiConfig?.task_ai.api_key_configured ? <Tag color="green">已配置密钥</Tag> : <Tag color="gold">未配置密钥</Tag>}</Descriptions.Item>
                          <Descriptions.Item label="做题草稿">{aiConfigCheck?.ready_for_task_draft ? <Tag color="green">可用</Tag> : <Tag color="gold">本地草稿兜底</Tag>}</Descriptions.Item>
                        </Descriptions>
                        <Input value={configForm.task_ai.base_url} onChange={(event) => updateProviderForm("task_ai", { base_url: event.target.value })} placeholder="做题 AI 接口地址，例如 https://api.openai.com/v1" />
                        <Input.Password value={configForm.task_ai.api_key} onChange={(event) => updateProviderForm("task_ai", { api_key: event.target.value })} placeholder={aiConfig?.task_ai.api_key_configured ? "留空则保留做题 AI 密钥" : "做题 AI 密钥"} />
                        <Input value={configForm.task_ai.model} onChange={(event) => updateProviderForm("task_ai", { model: event.target.value })} placeholder="做题 AI 模型名" />
                        <Input type="number" value={configForm.task_ai.timeout_seconds} onChange={(event) => updateProviderForm("task_ai", { timeout_seconds: Number(event.target.value) || 30 })} placeholder="超时时间秒" />
                        <Input.TextArea rows={4} value={configForm.task_ai.pre_prompt} onChange={(event) => updateProviderForm("task_ai", { pre_prompt: event.target.value })} placeholder="系统 AI 注入给做题 AI 的前置提示词" />
                        <Input.TextArea rows={3} value={configForm.task_ai.skills_text} onChange={(event) => updateProviderForm("task_ai", { skills_text: event.target.value })} placeholder="做题 AI 能力插件，每行一个或逗号分隔" />
                        <Input.TextArea rows={3} value={configForm.task_ai.md_files_text} onChange={(event) => updateProviderForm("task_ai", { md_files_text: event.target.value })} placeholder="做题 AI 可参考 md 文件路径，每行一个" />
                        <Space wrap>
                          <Button type="primary" loading={configLoading} onClick={() => void saveAiConfig()}>保存双 AI 配置</Button>
                          <Button onClick={async () => setAiConfigCheck(await fetchAiConfigCheck())}>重新自检</Button>
                        </Space>
                      </Space>
                    </Card>
                  </Col>
                  <Col xs={24} xl={14}>
                    <Card title="按题型配置">
                      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                        <Descriptions bordered size="small" column={2}>
                          <Descriptions.Item label="题型能力">bon8 视频/图片质量类题目</Descriptions.Item>
                          <Descriptions.Item label="任务ID">{BON8_TASK_ID}</Descriptions.Item>
                          <Descriptions.Item label="提示词">评分维度、选项排序、理由生成和异常暂停策略</Descriptions.Item>
                          <Descriptions.Item label="输出格式">答案、理由、置信度、提交内容检查、计时证据</Descriptions.Item>
                          <Descriptions.Item label="字段映射">题面材料、媒体证据、选项、暂存内容、提交回读</Descriptions.Item>
                          <Descriptions.Item label="护栏">能力发布确认一次、真实提交确认、失败暂停、审计写入</Descriptions.Item>
                          <Descriptions.Item label="账号范围">已同步 Cookie 且有 bon8 任务的账号</Descriptions.Item>
                          <Descriptions.Item label="执行模式">账号并行、账号内串行；任务操作台启动后台循环</Descriptions.Item>
                        </Descriptions>
                        <List
                          size="small"
                          bordered
                          header="配置自检明细"
                          dataSource={aiConfigCheck?.checks ?? []}
                          renderItem={(item) => (
                            <List.Item>
                              <Space direction="vertical" size={2} style={{ width: "100%" }}>
                                <Space wrap>
                                  <Tag color={statusColor[item.status] ?? "blue"}>{item.status}</Tag>
                                  <Typography.Text strong>{item.title}</Typography.Text>
                                </Space>
                                <Typography.Text>{item.detail}</Typography.Text>
                                <Typography.Text type="secondary">下一步：{item.next_step}</Typography.Text>
                              </Space>
                            </List.Item>
                          )}
                        />
                      </Space>
                    </Card>
                  </Col>
                </Row>
              </Space>
            ),
          },
          {
            key: "run-records",
            label: "运行记录",
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <Row gutter={[16, 16]}>
                  <Col xs={24} md={8}><Card><Statistic title="AI 队列总数" value={queue?.total ?? 0} /></Card></Col>
                  <Col xs={24} md={8}><Card><Statistic title="评分样本" value={scoreCases?.total ?? 0} /></Card></Col>
                  <Col xs={24} md={8}><Card><Statistic title="评分稳定样本" value={scoreSummary?.gate.manual_stable_count ?? 0} suffix={`/ ${scoreSummary?.gate.required_stable_count ?? 3}`} /></Card></Col>
                </Row>
                <Card title="评分题记录">
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    <List
                      size="small"
                      header="真题前就绪检查"
                      dataSource={scoreSummary?.readiness_checks ?? []}
                      renderItem={(item) => (
                        <List.Item>
                          <Space direction="vertical" size={2} style={{ width: "100%" }}>
                            <Space wrap>
                              <Tag color={statusColor[item.status] ?? "blue"}>{item.status}</Tag>
                              <Typography.Text strong>{item.title}</Typography.Text>
                              {item.required ? <Tag color="red">必需</Tag> : <Tag>可选</Tag>}
                            </Space>
                            <Typography.Text>{item.detail}</Typography.Text>
                            <Typography.Text type="secondary">下一步：{item.next_step}</Typography.Text>
                          </Space>
                        </List.Item>
                      )}
                    />
                    <List size="small" header="生产护栏" dataSource={scoreSummary?.guardrails ?? []} renderItem={(item) => <List.Item>{item}</List.Item>} />
                    <Table columns={scoreColumns} dataSource={scoreCases?.items ?? []} rowKey="id" pagination={{ pageSize: 5 }} />
                  </Space>
                </Card>
                <Card title="AI 队列">
                  <Table columns={columns} dataSource={queue?.items ?? []} rowKey="id" />
                </Card>
              </Space>
            ),
          },
        ]}
      />
    </div>
  );
}
