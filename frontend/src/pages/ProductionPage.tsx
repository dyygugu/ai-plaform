import { Alert, Button, Card, Col, Descriptions, Row, Space, Statistic, Steps, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import {
  fetchDomainSwitchRunbook,
  fetchReleaseGate,
  fetchSchedulerPlan,
  runSchedulerTick,
  type DomainSwitchRunbookResponse,
  type DomainSwitchRunbookStep,
  type ReleaseGateCheckItem,
  type ReleaseGateResponse,
  type SchedulerJobPlanItem,
  type SchedulerTickResponse,
} from "../api/client";

const statusColor: Record<string, string> = {
  passed: "green",
  completed: "green",
  warning: "gold",
  blocked: "gold",
  failed: "red",
  running: "blue",
};

function safeError(error: unknown): string {
  return error instanceof Error ? error.message : "接口请求失败";
}

export function ProductionPage() {
  const [gate, setGate] = useState<ReleaseGateResponse | null>(null);
  const [scheduler, setScheduler] = useState<SchedulerJobPlanItem[]>([]);
  const [dueCount, setDueCount] = useState(0);
  const [runbook, setRunbook] = useState<DomainSwitchRunbookResponse | null>(null);
  const [tick, setTick] = useState<SchedulerTickResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [gateResponse, schedulerResponse, runbookResponse] = await Promise.all([
        fetchReleaseGate(),
        fetchSchedulerPlan(),
        fetchDomainSwitchRunbook(),
      ]);
      setGate(gateResponse);
      setScheduler(schedulerResponse.jobs);
      setDueCount(schedulerResponse.due_count);
      setRunbook(runbookResponse);
    } catch (err: unknown) {
      message.error(safeError(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const onTick = async (dryRun: boolean) => {
    try {
      const result = await runSchedulerTick(dryRun, 10);
      setTick(result);
      message.success(result.message);
      await load();
    } catch (err: unknown) {
      message.error(safeError(err));
    }
  };

  const schedulerColumns: ColumnsType<SchedulerJobPlanItem> = [
    { title: "任务", dataIndex: "title", key: "title" },
    { title: "间隔", dataIndex: "interval_minutes", key: "interval_minutes", render: (value: number) => value ? `${value} 分钟` : "不调度" },
    { title: "最近运行", dataIndex: "last_run_at", key: "last_run_at", render: (value: string | null) => value ?? "未运行" },
    { title: "下次运行", dataIndex: "next_run_at", key: "next_run_at", render: (value: string | null) => value ?? "-" },
    { title: "到期", dataIndex: "due", key: "due", render: (value: boolean) => <Tag color={value ? "gold" : "green"}>{value ? "到期" : "未到期"}</Tag> },
    { title: "最近状态", dataIndex: "last_status", key: "last_status", render: (value: string | null) => value ? <Tag color={statusColor[value] ?? "default"}>{value}</Tag> : <Tag>无</Tag> },
  ];

  const gateColumns: ColumnsType<ReleaseGateCheckItem> = [
    { title: "门禁", dataIndex: "title", key: "title" },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
    { title: "必需", dataIndex: "required", key: "required", render: (value: boolean) => value ? "是" : "否" },
    { title: "说明", dataIndex: "message", key: "message" },
  ];

  const stepColumns: ColumnsType<DomainSwitchRunbookStep> = [
    { title: "序号", dataIndex: "order", key: "order", width: 80 },
    { title: "步骤", dataIndex: "title", key: "title" },
    { title: "操作", dataIndex: "command_or_action", key: "command_or_action" },
    { title: "预期", dataIndex: "expected_result", key: "expected_result" },
    { title: "回滚提示", dataIndex: "rollback_note", key: "rollback_note" },
  ];

  return (
    <div className="page-stack">
      <Space align="center" wrap>
        <Typography.Title level={2} style={{ margin: 0 }}>生产护栏</Typography.Title>
        <Button onClick={load} loading={loading}>刷新</Button>
        <Button onClick={() => onTick(true)}>调度预演</Button>
        <Button type="primary" onClick={() => onTick(false)}>执行到期任务</Button>
      </Space>
      <Alert type="warning" showIcon message="不会自动切换正式域名" description="此页只展示调度、门禁和 Runbook。manage.51gugu.uk 的反代修改仍由你人工完成。" />
      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}><Card><Statistic title="发布门禁" value={gate?.ready_for_manual_domain_switch ? "通过" : "未通过"} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="到期任务" value={dueCount} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="Runbook 步骤" value={runbook?.steps.length ?? 0} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="人工切换" value={runbook?.manual_only ? "必须" : "否"} /></Card></Col>
      </Row>
      <Card title="调度 Tick 结果">
        <Descriptions column={4} size="small">
          <Descriptions.Item label="模式">{tick?.dry_run ? "dry-run" : tick ? "execute" : "未运行"}</Descriptions.Item>
          <Descriptions.Item label="到期">{tick?.due_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="执行">{tick?.executed_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="跳过">{tick?.skipped_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="消息" span={4}>{tick?.message ?? "点击调度预演或执行到期任务"}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="调度计划">
        <Table columns={schedulerColumns} dataSource={scheduler} rowKey="job_key" pagination={false} />
      </Card>
      <Card title="发布门禁摘要">
        <Descriptions column={2} size="small">
          <Descriptions.Item label="测试入口">{gate?.public_base_url ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="正式域名">{gate?.production_domain ?? "manage.51gugu.uk"}</Descriptions.Item>
          <Descriptions.Item label="结论" span={2}>{gate?.message ?? "等待接口返回"}</Descriptions.Item>
        </Descriptions>
        <Table columns={gateColumns} dataSource={gate?.checks ?? []} rowKey="key" pagination={false} />
      </Card>
      <Card title="正式域名手动切换 Runbook">
        <Steps direction="vertical" size="small" current={-1} items={(runbook?.steps ?? []).map((item) => ({ title: item.title, description: `${item.command_or_action} / 预期：${item.expected_result}` }))} />
        <Table columns={stepColumns} dataSource={runbook?.rollback_steps ?? []} rowKey="order" pagination={false} title={() => "回滚步骤"} />
      </Card>
    </div>
  );
}
