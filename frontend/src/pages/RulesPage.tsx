import { Alert, Button, Card, Col, Descriptions, Empty, Form, Input, Row, Select, Space, Statistic, Steps, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useRef, useState } from "react";

import {
  approveTaskAbilityDraft,
  approveTaskAbilityRealNoSubmit,
  createTaskAbilityDraft,
  fetchAccounts,
  fetchTaskAbilityDrafts,
  runTaskAbilityRealNoSubmit,
  sendAiChat,
  type AccountItem,
  type TaskAbilityRealNoSubmitResponse,
  type TaskAbilityDraftItem,
  type TaskAbilityDraftListResponse,
} from "../api/client";

type AbilityBuildForm = {
  taskName: string;
  taskId: string;
  specificRules: string;
  sampleData: string;
  relatedContent: string;
};

function safeError(error: unknown): string {
  return error instanceof Error ? error.message : "接口请求失败";
}

export function RulesPage() {
  const [summary, setSummary] = useState<TaskAbilityDraftListResponse | null>(null);
  const [abilityDraft, setAbilityDraft] = useState<TaskAbilityDraftItem | null>(null);
  const [selectedDraftId, setSelectedDraftId] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [flowActionLoading, setFlowActionLoading] = useState(false);
  const [realNoSubmitResult, setRealNoSubmitResult] = useState<TaskAbilityRealNoSubmitResponse | null>(null);
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [realNoSubmitAccountId, setRealNoSubmitAccountId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [form] = Form.useForm<AbilityBuildForm>();
  const draftDetailRef = useRef<HTMLDivElement | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const nextSummary = await fetchTaskAbilityDrafts();
      const nextAccounts = await fetchAccounts();
      setSummary(nextSummary);
      setAccounts(nextAccounts);
      setAbilityDraft((current) => current ?? nextSummary.latest_draft);
      setSelectedDraftId((current) => current || nextSummary.latest_draft?.id || "");
      setRealNoSubmitAccountId((current) => current || nextAccounts[0]?.user_id || "");
      if (abilityDraft) {
        const refreshed = nextSummary.items.find((item) => item.id === abilityDraft.id);
        if (refreshed) setAbilityDraft(refreshed);
      }
    } catch (err: unknown) {
      setError(safeError(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const buildAbilityDraft = async (values: AbilityBuildForm) => {
    setBuilding(true);
    try {
      const userSubmission = {
        task_name: values.taskName,
        task_id: values.taskId,
        specific_rules: values.specificRules,
        sample_data: values.sampleData,
        related_content: values.relatedContent,
      };
      const aiResult = await sendAiChat({
        use_provider: true,
        message: [
          "请把我提交的特定规则、样例数据和相关内容制作成任务定制 AI 做题能力草稿。",
          "输出中文能力说明，包含：适用任务、读题材料、判断规则、输出格式、字段映射、护栏、人工确认点、下一步验证。",
          JSON.stringify(userSubmission, null, 2),
        ].join("\n\n"),
      });
      const draft = await createTaskAbilityDraft({
        ...userSubmission,
        system_ai_draft: aiResult.answer,
        system_ai_trace_id: aiResult.trace_id,
        provider_status: aiResult.provider_status,
      });
      setAbilityDraft(draft);
      setSelectedDraftId(draft.id);
      setRealNoSubmitResult(null);
      await load();
      message.success("系统 AI 已制作能力草稿");
    } catch (err: unknown) {
      message.error(safeError(err));
    } finally {
      setBuilding(false);
    }
  };

  const viewDraft = (record: TaskAbilityDraftItem) => {
    setAbilityDraft(record);
    setSelectedDraftId(record.id);
    setRealNoSubmitResult(null);
    message.info(`正在查看：${record.task_name}`);
    window.setTimeout(() => draftDetailRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }), 0);
  };

  const refreshSelectedDraft = async (draftId: string) => {
    const nextSummary = await fetchTaskAbilityDrafts();
    setSummary(nextSummary);
    const refreshed = nextSummary.items.find((item) => item.id === draftId) ?? nextSummary.latest_draft;
    setAbilityDraft(refreshed ?? null);
    setSelectedDraftId(refreshed?.id ?? "");
    return refreshed ?? null;
  };

  const approveSelectedDraft = async () => {
    if (!abilityDraft) return;
    setFlowActionLoading(true);
    try {
      await approveTaskAbilityDraft(abilityDraft.id);
      await refreshSelectedDraft(abilityDraft.id);
      message.success("草稿已确认，可以进入真实题不提交");
    } catch (err: unknown) {
      message.error(safeError(err));
    } finally {
      setFlowActionLoading(false);
    }
  };

  const runSelectedRealNoSubmit = async () => {
    if (!abilityDraft) return;
    setFlowActionLoading(true);
    try {
      const result = await runTaskAbilityRealNoSubmit(abilityDraft.id, {
        account_user_id: realNoSubmitAccountId,
        use_system_ai_for_vision: true,
      });
      setRealNoSubmitResult(result);
      await refreshSelectedDraft(abilityDraft.id);
      message.success(result.saved_to_task_ui ? "AI答案已保存到真实做题界面，请去页面审核" : "真实题不提交已生成待审核结果");
    } catch (err: unknown) {
      message.error(safeError(err));
    } finally {
      setFlowActionLoading(false);
    }
  };

  const approveSelectedRealNoSubmit = async () => {
    if (!abilityDraft) return;
    setFlowActionLoading(true);
    try {
      await approveTaskAbilityRealNoSubmit(abilityDraft.id);
      await refreshSelectedDraft(abilityDraft.id);
      message.success("已启用有做题能力");
    } catch (err: unknown) {
      message.error(safeError(err));
    } finally {
      setFlowActionLoading(false);
    }
  };

  const flowStage = abilityDraft?.flow_stage || (abilityDraft ? "draft_ready" : "rules_submitted");
  const submitCapabilityLocked = !abilityDraft?.capability_enabled;

  const flowCurrent = abilityDraft?.capability_enabled
    ? 3
    : flowStage === "real_no_submit_review"
      ? 2
      : flowStage === "real_no_submit_ready" || flowStage === "real_no_submit_claim_required" || flowStage === "real_no_submit_blocked"
        ? 2
        : abilityDraft
          ? 1
          : 0;

  const queueSnapshot = abilityDraft?.task_queue_snapshot ?? {};
  const review = abilityDraft?.real_no_submit_review ?? {};
  const queuePending = Number(queueSnapshot.pending ?? 0);
  const queueProcessing = Number(queueSnapshot.processing ?? 0);
  const queueRepair = Number(queueSnapshot.repair ?? 0);
  const hasExecutableItem = Boolean(queueSnapshot.has_executable_item ?? (queuePending > 0 || queueProcessing > 0 || queueRepair > 0));
  const claimRequired = Boolean(queueSnapshot.claim_required);
  const reviewSourceMode = String(realNoSubmitResult?.question_context?.source_mode || review.source_mode || "-");
  const reviewEvidencePath = String(realNoSubmitResult?.question_context?.evidence_path || review.evidence_path || "-");
  const selectedRealNoSubmitAccount = accounts.find((account) => account.user_id === realNoSubmitAccountId);
  const selectedRealNoSubmitAccountLabel = selectedRealNoSubmitAccount ? `${selectedRealNoSubmitAccount.display_name || selectedRealNoSubmitAccount.user_id} / ${selectedRealNoSubmitAccount.user_id}` : realNoSubmitAccountId;
  const reviewAccount = String(realNoSubmitResult?.queue_snapshot?.account_name || realNoSubmitResult?.queue_snapshot?.account_user_id || review.account_name || review.account_user_id || queueSnapshot.account_name || queueSnapshot.account_user_id || "待执行时选择");
  const sourceNeedsAttention = reviewSourceMode === "local-evidence-real-task-sample";
  const accountOptions = accounts.map((account) => ({
    value: account.user_id,
    label: `${account.display_name || account.user_id} / ${account.user_id}`,
  }));

  const draftColumns: ColumnsType<TaskAbilityDraftItem> = [
    { title: "能力版本", dataIndex: "version", key: "version", width: 160 },
    { title: "任务名称", dataIndex: "task_name", key: "task_name" },
    { title: "任务ID", dataIndex: "task_id", key: "task_id", width: 190 },
    { title: "状态", dataIndex: "status", key: "status", width: 90, render: (value: string) => <Tag>{value}</Tag> },
    { title: "下一步", dataIndex: "next_step", key: "next_step" },
    {
      title: "操作",
      key: "actions",
      width: 90,
      render: (_, record) => (
        <Button size="small" type={selectedDraftId === record.id ? "primary" : "default"} onClick={() => viewDraft(record)}>
          {selectedDraftId === record.id ? "正在查看" : "查看草稿"}
        </Button>
      ),
    },
  ];

  return (
    <div className="page-stack">
      <Space align="center" wrap>
        <div>
          <Typography.Title level={2} style={{ margin: 0 }}>题型能力库</Typography.Title>
          <Typography.Text type="secondary">能力版本来自你提交的特定规则、样例数据和相关内容，再交由系统 AI 制作为任务定制能力草稿。</Typography.Text>
        </div>
      </Space>
      {error ? <Alert type="warning" message="任务能力草稿接口暂不可用" description={error} showIcon /> : null}
      <Alert
        type="info"
        showIcon
        message="AI 做题统一入口"
        description="给系统 AI 和做题执行器读取；人工只看中文摘要。AI 做题相关的建立、执行和操控台入口统一从题型能力库开始：先提交规则生成能力草稿，再查看草稿，之后执行真实题不提交并由人工审核，最后才启用有做题能力（端到端做题提交）。"
      />
      <Card title="AI 做题统一流程">
        <Steps
          current={flowCurrent}
          items={[
            { title: "提交规则", description: "提交特定规则、样例数据和相关内容。" },
            { title: "查看草稿", description: "人工审核系统 AI 生成的中文能力草稿。" },
            { title: "端到端做题不提交", description: "真实找一道可执行题，把 AI 答案保存到做题界面，但不正式提交。" },
            { title: "有做题能力（端到端做题提交）", description: "你审核真实不提交结果通过后，才标记可完整做题。" },
          ]}
        />
      </Card>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}><Card><Statistic title="任务定制能力版本" value={summary?.total ?? 0} /></Card></Col>
        <Col xs={24} md={8}><Card><Statistic title="当前草稿" value={abilityDraft?.version ?? "未制作"} /></Card></Col>
        <Col xs={24} md={8}><Card><Statistic title="发布前要求" value="审核 + 真题验证" /></Card></Col>
      </Row>
      <Card title="提交材料，让系统 AI 制作任务定制能力版本">
        <Form form={form} layout="vertical" onFinish={(values) => void buildAbilityDraft(values)}>
          <Row gutter={[16, 0]}>
            <Col xs={24} md={12}>
              <Form.Item label="任务名称" name="taskName" rules={[{ required: true, message: "请填写任务名称" }]}>
                <Input placeholder="例如 bon8 草图与流程图正式队列" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="任务ID" name="taskId" rules={[{ required: true, message: "请填写任务ID" }]}>
                <Input placeholder="例如 7637771731901861641" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="特定规则" name="specificRules" rules={[{ required: true, message: "请填写这类题的判断规则" }]}>
            <Input.TextArea rows={4} placeholder="写给系统 AI：这个任务怎么判断、怎么选、哪些情况要暂停。" />
          </Form.Item>
          <Form.Item label="样例数据" name="sampleData" rules={[{ required: true, message: "请粘贴样例题面、选项或录制摘要" }]}>
            <Input.TextArea rows={4} placeholder="粘贴样例题面、选项、字段、录制包摘要、成功/失败案例。" />
          </Form.Item>
          <Form.Item label="相关内容" name="relatedContent">
            <Input.TextArea rows={3} placeholder="补充截图路径、接口说明、历史提交证据、注意事项。" />
          </Form.Item>
          <Space wrap>
            <Button type="primary" htmlType="submit" loading={building}>系统 AI 制作能力草稿</Button>
            <Typography.Text type="secondary">制作后先生成草稿，人工审核并用真实题验证后，才发布为任务定制能力版本。</Typography.Text>
          </Space>
        </Form>
      </Card>
      <div ref={draftDetailRef} />
      <Card title="查看草稿">
        {abilityDraft ? (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert type="success" showIcon message={`正在查看草稿：${abilityDraft.task_name}`} description="这份草稿是后续真实题不提交、人工审核和启用做题能力的唯一入口来源。" />
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="能力版本">{abilityDraft.version}</Descriptions.Item>
              <Descriptions.Item label="状态">{abilityDraft.status}</Descriptions.Item>
              <Descriptions.Item label="任务名称">{abilityDraft.task_name}</Descriptions.Item>
              <Descriptions.Item label="任务ID">{abilityDraft.task_id}</Descriptions.Item>
              <Descriptions.Item label="特定规则" span={2}><pre className="pre-wrap">{abilityDraft.specific_rules}</pre></Descriptions.Item>
              <Descriptions.Item label="样例数据" span={2}><pre className="pre-wrap">{abilityDraft.sample_data}</pre></Descriptions.Item>
              <Descriptions.Item label="系统 AI 草稿" span={2}><pre className="pre-wrap">{abilityDraft.system_ai_draft}</pre></Descriptions.Item>
              <Descriptions.Item label="下一步" span={2}>{abilityDraft.next_step}</Descriptions.Item>
            </Descriptions>
            <Space wrap>
              <Button type="primary" disabled={flowStage !== "draft_ready"} loading={flowActionLoading} onClick={() => void approveSelectedDraft()}>
                草稿无误，进入端到端不提交
              </Button>
              {flowStage !== "draft_ready" ? <Tag color="green">草稿已确认</Tag> : <Typography.Text type="secondary">确认后才允许真实题不提交。</Typography.Text>}
            </Space>
          </Space>
        ) : (
          <Empty description="还没有任务定制能力草稿，先提交材料再制作。" />
        )}
      </Card>
      <Card title="端到端做题不提交">
        {abilityDraft ? (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert
              type={flowStage === "real_no_submit_review" ? "success" : "warning"}
              showIcon
              message={flowStage === "real_no_submit_review" ? "真实题暂存结果待你审核" : "真实题不提交会保存到做题界面"}
              description="这里根据待处理、处理中、返修判断是否有题；处理中/返修可直接执行，仅待处理有题时需要先显式获取一道题。本步骤只允许调用 SubmitTempItemAnswer 保存 AI 答案到做题界面，不会正式提交。"
            />
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="当前任务">{abilityDraft.task_name}</Descriptions.Item>
              <Descriptions.Item label="任务ID">{abilityDraft.task_id}</Descriptions.Item>
              <Descriptions.Item label="待处理">{queuePending}</Descriptions.Item>
              <Descriptions.Item label="处理中">{queueProcessing}</Descriptions.Item>
              <Descriptions.Item label="返修">{queueRepair}</Descriptions.Item>
              <Descriptions.Item label="上次执行账号">{reviewAccount}</Descriptions.Item>
              <Descriptions.Item label="本次执行账号" span={2}>{selectedRealNoSubmitAccountLabel}</Descriptions.Item>
              <Descriptions.Item label="是否有题" span={2}>{hasExecutableItem ? "有题可执行" : "等待刷新或暂无题"}</Descriptions.Item>
              <Descriptions.Item label="执行边界" span={2}>真实题不提交；保存 AI 答案到做题界面供你审核，但不正式提交。</Descriptions.Item>
            </Descriptions>
            <Space wrap align="center">
              <Typography.Text>选择做题账号</Typography.Text>
              <Select
                style={{ minWidth: 320 }}
                value={realNoSubmitAccountId}
                options={accountOptions}
                placeholder="暂无可用账号"
                disabled={!accountOptions.length}
                onChange={setRealNoSubmitAccountId}
                showSearch
                optionFilterProp="label"
              />
              <Tag color="blue">本轮使用系统 AI 看图评分</Tag>
            </Space>
            <Space wrap>
              <Button type="primary" disabled={flowStage !== "real_no_submit_ready"} loading={flowActionLoading} onClick={() => void runSelectedRealNoSubmit()}>
                真实做一道题但不提交
              </Button>
              <Button disabled={flowStage !== "real_no_submit_review"} loading={flowActionLoading} onClick={() => void runSelectedRealNoSubmit()}>
                重新执行端到端不提交
              </Button>
              {claimRequired ? <Tag color="gold">需要先获取一道题</Tag> : null}
              <Typography.Text type="secondary">执行成功后请去真实页面核对，再点下一步启用能力。</Typography.Text>
            </Space>
            {sourceNeedsAttention ? (
              <Alert
                type="warning"
                showIcon
                message="当前记录来自录制证据，不能算端到端"
                description="录制题目通常已经提交过，不能用于真实端到端不提交。请用正确账号打开或获取当前真实题后，点击重新执行端到端不提交。"
              />
            ) : null}
            {realNoSubmitResult || Object.keys(review).length ? (
              <Descriptions bordered size="small" column={2}>
                <Descriptions.Item label="阶段">{realNoSubmitResult?.stage || String(review.stage || "-")}</Descriptions.Item>
                <Descriptions.Item label="审核状态">{realNoSubmitResult?.review_status || String(review.review_status || "-")}</Descriptions.Item>
                <Descriptions.Item label="写入做题界面">{realNoSubmitResult?.saved_to_task_ui || review.saved_to_task_ui ? "已保存" : "未保存"}</Descriptions.Item>
                <Descriptions.Item label="正式提交">{realNoSubmitResult?.submits_remote ? "是" : "否"}</Descriptions.Item>
                <Descriptions.Item label="执行账号" span={2}>{reviewAccount}</Descriptions.Item>
                <Descriptions.Item label="题目ID" span={2}>{String(realNoSubmitResult?.question_context?.item_id || review.item_id || "-")}</Descriptions.Item>
                <Descriptions.Item label="题面来源" span={2}>{reviewSourceMode}</Descriptions.Item>
                <Descriptions.Item label="来源证据" span={2}>{reviewEvidencePath}</Descriptions.Item>
                <Descriptions.Item label="写入接口" span={2}>SubmitTempItemAnswer；禁止 SubmitItem/继续下一题/领取/放弃。</Descriptions.Item>
                <Descriptions.Item label="审核件路径" span={2}>{realNoSubmitResult?.review_artifact_path || String(review.review_artifact_path || "-")}</Descriptions.Item>
                <Descriptions.Item label="AI填写结果" span={2}><pre className="pre-wrap">{JSON.stringify(realNoSubmitResult?.saved_answer ?? realNoSubmitResult?.answer_preview ?? review, null, 2)}</pre></Descriptions.Item>
                <Descriptions.Item label="结果说明" span={2}>{realNoSubmitResult?.ui_review_hint || realNoSubmitResult?.message || String(review.ui_review_hint || abilityDraft.next_step)}</Descriptions.Item>
              </Descriptions>
            ) : null}
          </Space>
        ) : (
          <Empty description="先查看一个能力草稿，再做端到端不提交演练。" />
        )}
      </Card>
      <Card title="有做题能力（端到端做题提交）">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Alert
            type={submitCapabilityLocked ? "warning" : "success"}
            showIcon
            message={submitCapabilityLocked ? "做题能力未启用" : "有做题能力"}
            description="只有草稿审核、真实题不提交审核件和你的人工确认都通过后，才标记为有做题能力。正式提交仍属于高风险动作，需要单独确认。"
          />
          <Space wrap>
            <Button type="primary" disabled={flowStage !== "real_no_submit_review"} loading={flowActionLoading} onClick={() => void approveSelectedRealNoSubmit()}>
              我已审核通过，启用做题能力
            </Button>
            {abilityDraft?.capability_enabled ? <Tag color="green">已启用</Tag> : <Typography.Text type="secondary">未审核真实题不提交结果前不会启用。</Typography.Text>}
          </Space>
        </Space>
      </Card>
      <Card title="能力草稿记录" extra={<Button onClick={() => void load()} loading={loading}>刷新</Button>}>
        <Table
          columns={draftColumns}
          dataSource={summary?.items ?? []}
          rowKey="id"
          loading={loading}
          pagination={{ pageSize: 6 }}
          rowClassName={(record) => (record.id === selectedDraftId ? "selected-task-row" : "")}
          scroll={{ x: "max-content" }}
        />
      </Card>
    </div>
  );
}
