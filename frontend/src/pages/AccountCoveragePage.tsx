import { Alert, Button, Card, Descriptions, List, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import {
  createAccountCoverageBaseline,
  fetchAccountCoverageSummary,
  type AccountCoverageSummaryResponse,
  type AccountTaskCoverageRow,
  type TaskCoverageItem,
} from "../api/client";

const statusColor: Record<string, string> = {
  passed: "green",
  covered: "green",
  warning: "orange",
  pending_sampling: "gold",
  source_empty: "red",
  needs_login: "red",
  stale: "gold",
  active: "green",
  review: "blue",
};

const matrixColumns: ColumnsType<AccountTaskCoverageRow> = [
  { title: "账号", dataIndex: "display_name", key: "display_name" },
  { title: "用户ID", dataIndex: "user_id", key: "user_id" },
  { title: "来源", dataIndex: "is_task_source", key: "is_task_source", render: (value: boolean) => (value ? <Tag color="blue">主来源</Tag> : <Tag>迁移账号</Tag>) },
  { title: "任务数", dataIndex: "task_count", key: "task_count" },
  { title: "待处理", dataIndex: "pending_total", key: "pending_total" },
  { title: "覆盖状态", dataIndex: "coverage_status", key: "coverage_status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
  { title: "登录复核", dataIndex: "login_review_status", key: "login_review_status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
  { title: "建议", dataIndex: "recommended_action", key: "recommended_action" },
];

const taskColumns: ColumnsType<TaskCoverageItem> = [
  { title: "任务简称", dataIndex: "task_short_name", key: "task_short_name" },
  { title: "任务ID", dataIndex: "task_id", key: "task_id" },
  { title: "覆盖账号", dataIndex: "covered_account_count", key: "covered_account_count" },
  { title: "待处理合计", dataIndex: "pending_total", key: "pending_total" },
  { title: "状态", dataIndex: "status_raw", key: "status_raw" },
];

export function AccountCoveragePage() {
  const [summary, setSummary] = useState<AccountCoverageSummaryResponse | null>(null);
  const [reportPath, setReportPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const reload = async () => setSummary(await fetchAccountCoverageSummary());
  useEffect(() => { void reload(); }, []);

  const onCreateBaseline = async () => {
    setLoading(true);
    try {
      const result = await createAccountCoverageBaseline();
      setSummary(result.summary);
      setReportPath(result.report_path);
      message.success("覆盖基线已生成");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page-stack">
      <Space align="center" style={{ justifyContent: "space-between", width: "100%" }}>
        <Typography.Title level={2} style={{ margin: 0 }}>账号覆盖</Typography.Title>
        <Button type="primary" loading={loading} onClick={onCreateBaseline}>生成覆盖基线</Button>
      </Space>
      <Alert
        type="info"
        showIcon
        message="多账号任务覆盖与登录态复核"
        description="本页用于 7 个迁移账号的覆盖矩阵、主任务来源和登录态复核；不复制 Cookie、不触发外部系统，正式域名仍在生产验收完成后提醒。"
      />
      <Card title="覆盖摘要">
        <Descriptions bordered size="small" column={4}>
          <Descriptions.Item label="状态"><Tag color={statusColor[summary?.status ?? "warning"]}>{summary?.status ?? "loading"}</Tag></Descriptions.Item>
          <Descriptions.Item label="账号数">{summary?.account_count ?? 0}/{summary?.expected_account_count ?? 7}</Descriptions.Item>
          <Descriptions.Item label="主来源任务">{summary?.source_task_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="覆盖账号">{summary?.covered_account_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="未覆盖账号">{summary?.uncovered_account_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="需登录">{summary?.needs_login_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="stale">{summary?.stale_count ?? 0}</Descriptions.Item>
          <Descriptions.Item label="最新证据">{reportPath ?? "生成后显示"}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="7 账号覆盖矩阵">
        <Table columns={matrixColumns} dataSource={summary?.matrix ?? []} rowKey="user_id" pagination={false} />
      </Card>
      <Card title="任务覆盖明细">
        <Table columns={taskColumns} dataSource={summary?.task_items ?? []} rowKey="task_id" pagination={{ pageSize: 5 }} />
      </Card>
      <Card title="风险提示与下一步">
        <List dataSource={[...(summary?.risk_notes ?? []), ...(summary?.next_actions ?? [])]} renderItem={(item) => <List.Item>{item}</List.Item>} />
      </Card>
    </div>
  );
}