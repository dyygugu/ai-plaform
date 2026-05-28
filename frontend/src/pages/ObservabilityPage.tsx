import { Alert, Button, Card, Col, Descriptions, Row, Space, Statistic, Table, Tag, Timeline, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import {
  fetchObservabilitySummary,
  runObservabilityProbes,
  type CollectorGuardResponse,
  type ObservabilityMetricItem,
  type ObservabilitySummary,
  type ProbeResultItem,
  type TimelineEventItem,
} from "../api/client";

const statusColor: Record<string, string> = {
  passed: "green",
  info: "blue",
  warning: "gold",
  failed: "red",
  error: "red",
  critical: "red",
  completed: "green",
};

function safeError(error: unknown): string {
  return error instanceof Error ? error.message : "接口请求失败";
}

export function ObservabilityPage() {
  const [summary, setSummary] = useState<ObservabilitySummary | null>(null);
  const [probeResults, setProbeResults] = useState<ProbeResultItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const result = await fetchObservabilitySummary();
      setSummary(result);
      setProbeResults(result.probes);
    } catch (err: unknown) {
      message.error(safeError(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const runProbes = async () => {
    try {
      const result = await runObservabilityProbes();
      setProbeResults(result.results);
      message.success(`探针完成：${result.status}`);
      await load();
    } catch (err: unknown) {
      message.error(safeError(err));
    }
  };

  const metrics = summary?.metrics ?? [];
  const collector = summary?.collector_guard;

  const metricColumns: ColumnsType<ObservabilityMetricItem> = [
    { title: "指标", dataIndex: "title", key: "title" },
    { title: "值", dataIndex: "value", key: "value", render: (value: ObservabilityMetricItem["value"]) => String(value) },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
    { title: "说明", dataIndex: "message", key: "message" },
  ];

  const probeColumns: ColumnsType<ProbeResultItem> = [
    { title: "探针", dataIndex: "title", key: "title" },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
    { title: "耗时", dataIndex: "latency_ms", key: "latency_ms", render: (value: number) => `${value} ms` },
    { title: "说明", dataIndex: "message", key: "message" },
  ];

  return (
    <div className="page-stack">
      <Space align="center" wrap>
        <Typography.Title level={2} style={{ margin: 0 }}>观测中心</Typography.Title>
        <Button onClick={load} loading={loading}>刷新</Button>
        <Button type="primary" onClick={runProbes}>运行探针</Button>
      </Space>
      <Alert type="info" showIcon message="采集守护保持只读安全模式" description="观测中心只读取脱敏样本、运行历史和审计摘要；不会执行真实写操作，也不会切换正式域名。" />
      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}><Card><Statistic title="整体状态" value={summary?.status ?? "加载中"} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="指标数" value={metrics.length} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="任务目录" value={collector?.task_count ?? 0} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="探针数" value={probeResults.length} /></Card></Col>
      </Row>
      <Card title="采集守护">
        <CollectorGuardDescriptions collector={collector} />
      </Card>
      <Card title="观测摘要">
        <Table columns={metricColumns} dataSource={metrics} rowKey="key" pagination={false} />
      </Card>
      <Card title="探针结果" extra={<Button onClick={runProbes}>重新运行</Button>}>
        <Table columns={probeColumns} dataSource={probeResults} rowKey="key" pagination={false} />
      </Card>
      <Card title="事件时间线">
        <Timeline items={(summary?.recent_timeline ?? []).map((item) => ({ color: timelineColor(item), children: <TimelineRow item={item} /> }))} />
      </Card>
    </div>
  );
}

function CollectorGuardDescriptions({ collector }: { collector: CollectorGuardResponse | undefined }) {
  return (
    <Descriptions column={2} size="small">
      <Descriptions.Item label="来源账号">{collector?.source_account_user_id ?? "-"}</Descriptions.Item>
      <Descriptions.Item label="安全模式">{collector?.safe_mode ? "只读" : "未知"}</Descriptions.Item>
      <Descriptions.Item label="只读 Cookie 可用">{collector?.live_readonly_available ? "是" : "否"}</Descriptions.Item>
      <Descriptions.Item label="样本存在">{collector?.sample_exists ? "是" : "否"}</Descriptions.Item>
      <Descriptions.Item label="样本年龄">{collector?.sample_age_minutes ?? "-"} 分钟</Descriptions.Item>
      <Descriptions.Item label="任务/错误">{collector ? `${collector.task_count}/${collector.error_count}` : "0/0"}</Descriptions.Item>
      <Descriptions.Item label="状态"><Tag color={statusColor[collector?.status ?? ""] ?? "default"}>{collector?.status ?? "unknown"}</Tag></Descriptions.Item>
      <Descriptions.Item label="说明">{collector?.message ?? "等待接口返回"}</Descriptions.Item>
      <Descriptions.Item label="摘要路径" span={2}>{collector?.sample_summary_path ?? "-"}</Descriptions.Item>
      <Descriptions.Item label="最近错误" span={2}>{collector?.latest_error ?? "无"}</Descriptions.Item>
    </Descriptions>
  );
}

function TimelineRow({ item }: { item: TimelineEventItem }) {
  return (
    <Space direction="vertical" size={2}>
      <Space wrap><Tag>{item.source}</Tag><Tag color={statusColor[item.severity] ?? "default"}>{item.severity}</Tag><Typography.Text strong>{item.title}</Typography.Text></Space>
      <Typography.Text>{item.message || "-"}</Typography.Text>
      <Typography.Text type="secondary">{item.created_at} · trace_id={item.trace_id}</Typography.Text>
    </Space>
  );
}

function timelineColor(item: TimelineEventItem): string {
  if (["failed", "error", "critical"].includes(item.severity)) return "red";
  if (["warning", "blocked"].includes(item.severity)) return "orange";
  return "green";
}
