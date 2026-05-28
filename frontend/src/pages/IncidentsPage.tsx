import { Alert, Button, Card, Col, List, Row, Space, Statistic, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import {
  closeIncidentLoop,
  fetchIncidentClosurePlan,
  fetchIncidentSummary,
  type IncidentClosureCheck,
  type IncidentClosurePlanResponse,
  type IncidentClosureResponse,
  type IncidentQueueItem,
  type IncidentRunbookItem,
  type IncidentSummaryResponse,
} from "../api/client";

const statusColor: Record<string, string> = {
  passed: "green",
  ready: "blue",
  open: "gold",
  warning: "gold",
  failed: "red",
  critical: "red",
};

const incidentColumns: ColumnsType<IncidentQueueItem> = [
  { title: "异常", dataIndex: "title", key: "title" },
  { title: "级别", dataIndex: "severity", key: "severity", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
  { title: "对象", dataIndex: "subject", key: "subject" },
  { title: "原因", dataIndex: "reason", key: "reason" },
  { title: "建议动作", dataIndex: "recommended_action", key: "recommended_action" },
  { title: "证据", dataIndex: "evidence_path", key: "evidence_path" },
];

const runbookColumns: ColumnsType<IncidentRunbookItem> = [
  { title: "Runbook", dataIndex: "title", key: "title" },
  { title: "分类", dataIndex: "category", key: "category" },
  { title: "级别", dataIndex: "severity", key: "severity", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
  { title: "触发条件", dataIndex: "trigger", key: "trigger" },
  { title: "负责人", dataIndex: "owner", key: "owner" },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
];

const closureCheckColumns: ColumnsType<IncidentClosureCheck> = [
  { title: "检查项", dataIndex: "title", key: "title" },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
  { title: "必需", dataIndex: "required", key: "required", render: (value: boolean) => value ? "是" : "否" },
  { title: "详情", dataIndex: "detail", key: "detail" },
  { title: "下一步", dataIndex: "next_step", key: "next_step" },
  { title: "证据", dataIndex: "evidence_path", key: "evidence_path" },
];

export function IncidentsPage() {
  const [data, setData] = useState<IncidentSummaryResponse | null>(null);
  const [plan, setPlan] = useState<IncidentClosurePlanResponse | null>(null);
  const [closure, setClosure] = useState<IncidentClosureResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    const [summary, closurePlan] = await Promise.all([fetchIncidentSummary(), fetchIncidentClosurePlan()]);
    setData(summary);
    setPlan(closurePlan);
  };
  useEffect(() => { load(); }, []);

  const onCloseLoop = async () => {
    setLoading(true);
    try {
      const result = await closeIncidentLoop();
      setClosure(result);
      setData(result.summary);
      setPlan(result.plan);
      message.success(`闭环记录已生成：${result.report_path}`);
    } finally {
      setLoading(false);
    }
  };

  return <div className="page-stack">
    <Space direction="vertical" size={4}>
      <Typography.Title level={2}>异常处置</Typography.Title>
      <Typography.Text type="secondary">异常处置与运维闭环：账号、采集、Worker、备份、审计和数据质量 runbook 统一收口。</Typography.Text>
    </Space>
    <Alert type={data?.status === "passed" ? "success" : "warning"} showIcon message={data?.status === "passed" ? "异常处置基线可用" : "异常处置基线需复核"} description="本页只生成异常闭环记录和审计 trace；飞书错误通知发送入口在告警中心配置，正式域名仍按生产护栏手动切换。" />
    <Row gutter={[16, 16]}>
      <Col xs={24} md={6}><Card><Statistic title="Open 异常" value={data?.total_open ?? 0} /></Card></Col>
      <Col xs={24} md={6}><Card><Statistic title="Critical" value={data?.critical_count ?? 0} /></Card></Col>
      <Col xs={24} md={6}><Card><Statistic title="Warning" value={data?.warning_count ?? 0} /></Card></Col>
      <Col xs={24} md={6}><Card><Statistic title="Runbook" value={data?.runbook_count ?? 0} /></Card></Col>
    </Row>
    <Card title="闭环检查清单" extra={<Tag color={plan?.ready_to_close ? "green" : "gold"}>{plan?.ready_to_close ? "可闭环" : "需处理"}</Tag>}>
      <Table columns={closureCheckColumns} dataSource={plan?.checks ?? []} rowKey="key" pagination={false} />
    </Card>
    <Card title="异常队列" extra={<Button type="primary" onClick={onCloseLoop} loading={loading}>生成闭环记录</Button>}>
      <Table columns={incidentColumns} dataSource={data?.incidents ?? []} rowKey="key" locale={{ emptyText: "当前无 open 异常" }} pagination={false} />
    </Card>
    <Card title="处置 Runbook">
      <Table columns={runbookColumns} dataSource={data?.runbooks ?? []} rowKey="key" expandable={{ expandedRowRender: (record) => <List dataSource={record.steps} renderItem={(item) => <List.Item>{item}</List.Item>} /> }} />
    </Card>
    {closure && <Alert type="success" showIcon message="最新闭环证据" description={<Space direction="vertical"><span>报告：{closure.report_path}</span><span>审计 trace：{closure.audit_trace_id}</span><span>dry_run：{String(closure.dry_run)}</span></Space>} />}
    <Row gutter={[16, 16]}>
      <Col xs={24} md={12}><Card title="风险提示"><List dataSource={data?.risk_notes ?? []} renderItem={(item) => <List.Item>{item}</List.Item>} /></Card></Col>
      <Col xs={24} md={12}><Card title="下一步"><List dataSource={data?.next_actions ?? []} renderItem={(item) => <List.Item>{item}</List.Item>} /></Card></Col>
    </Row>
  </div>;
}
