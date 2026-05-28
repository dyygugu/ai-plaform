import { Alert, Button, Card, Col, List, Row, Space, Statistic, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import {
  createDataQualityReport,
  exportDataQualityWorkbook,
  fetchDataQualitySummary,
  type DataQualityCheckItem,
  type DataQualityReportResponse,
  type DataQualitySummaryResponse,
  type EarningsContractItem,
} from "../api/client";

const statusColor: Record<string, string> = {
  passed: "green",
  sealed: "blue",
  warning: "gold",
  failed: "red",
};

const checkColumns: ColumnsType<DataQualityCheckItem> = [
  { title: "检查项", dataIndex: "title", key: "title" },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
  { title: "预期", dataIndex: "expected", key: "expected" },
  { title: "实际", dataIndex: "actual", key: "actual" },
  { title: "证据", dataIndex: "evidence_path", key: "evidence_path" },
];

const contractColumns: ColumnsType<EarningsContractItem> = [
  { title: "口径", dataIndex: "title", key: "title" },
  { title: "源字段", dataIndex: "source_field", key: "source_field" },
  { title: "展示名", dataIndex: "display_name", key: "display_name" },
  { title: "聚合规则", dataIndex: "aggregation", key: "aggregation" },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
];

export function DataQualityPage() {
  const [data, setData] = useState<DataQualitySummaryResponse | null>(null);
  const [report, setReport] = useState<DataQualityReportResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const load = () => fetchDataQualitySummary().then(setData);
  useEffect(() => { load(); }, []);

  const onExport = async () => {
    setLoading(true);
    try {
      const result = await exportDataQualityWorkbook();
      message.success(`增强 Excel 已导出：${result.export_path}`);
      await load();
    } finally {
      setLoading(false);
    }
  };

  const onReport = async () => {
    setLoading(true);
    try {
      const result = await createDataQualityReport();
      setReport(result);
      setData(result.summary);
      message.success(`数据校验报告已生成：${result.report_path}`);
    } finally {
      setLoading(false);
    }
  };

  return <div className="page-stack">
    <Space direction="vertical" size={4}>
      <Typography.Title level={2}>数据校验</Typography.Title>
      <Typography.Text type="secondary">数据正确性与收益口径：收益三项、7 账号覆盖、任务简称、待处理数字和证据路径统一核验。</Typography.Text>
    </Space>
    <Alert type={data?.status === "passed" ? "success" : "warning"} showIcon message={data?.status === "passed" ? "数据质量基线可用" : "数据质量基线需复核"} description="本页只生成本地校验、Excel 和审计证据；不复制 Cookie 明文，不触发外部系统，不切换正式域名。" />
    <Row gutter={[16, 16]}>
      <Col xs={24} md={6}><Card><Statistic title="账号覆盖" value={data?.account_count ?? 0} suffix={`/ ${data?.expected_account_count ?? 7}`} /></Card></Col>
      <Col xs={24} md={6}><Card><Statistic title="任务数量" value={data?.task_count ?? 0} /></Card></Col>
      <Col xs={24} md={6}><Card><Statistic title="收益行数" value={data?.earnings_row_count ?? 0} /></Card></Col>
      <Col xs={24} md={6}><Card><Statistic title="审计事件" value={data?.audit_event_count ?? 0} /></Card></Col>
    </Row>
    <Card title="收益核验口径" extra={<Space><Button onClick={onExport} loading={loading}>导出增强 Excel</Button><Button type="primary" onClick={onReport} loading={loading}>生成校验报告</Button></Space>}>
      <Table columns={contractColumns} dataSource={data?.contracts ?? []} rowKey="key" pagination={false} />
    </Card>
    <Card title="一致性检查">
      <Table columns={checkColumns} dataSource={data?.checks ?? []} rowKey="key" pagination={false} />
    </Card>
    {report && <Alert type="success" showIcon message="最新校验证据" description={<Space direction="vertical"><span>报告：{report.report_path}</span><span>Excel：{report.export_path}</span><span>审计 trace：{report.audit_trace_id}</span></Space>} />}
    <Row gutter={[16, 16]}>
      <Col xs={24} md={12}><Card title="风险提示"><List dataSource={data?.risk_notes ?? []} renderItem={(item) => <List.Item>{item}</List.Item>} /></Card></Col>
      <Col xs={24} md={12}><Card title="下一步"><List dataSource={data?.next_actions ?? []} renderItem={(item) => <List.Item>{item}</List.Item>} /></Card></Col>
    </Row>
  </div>;
}
