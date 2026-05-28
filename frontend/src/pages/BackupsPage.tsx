import { Card, Descriptions, Button, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import { fetchBackupJobs, fetchBackupPlan, runManualBackup, testBackupTarget, type BackupJobItem, type BackupPlanResponse } from "../api/client";

const columns: ColumnsType<BackupJobItem> = [
  { title: "类型", dataIndex: "backup_type", key: "backup_type" },
  { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={value === "completed" ? "green" : value === "failed" ? "red" : "gold"}>{value}</Tag> },
  { title: "目标", dataIndex: "target_path", key: "target_path" },
  { title: "说明", dataIndex: "message", key: "message" },
  { title: "trace_id", dataIndex: "trace_id", key: "trace_id" },
];

export function BackupsPage() {
  const [plan, setPlan] = useState<BackupPlanResponse | null>(null);
  const [jobs, setJobs] = useState<BackupJobItem[]>([]);

  const reload = () => { fetchBackupPlan().then(setPlan); fetchBackupJobs().then(setJobs); };
  useEffect(reload, []);

  const onTest = async () => { const result = await testBackupTarget(); message.success(result.message); };
  const onManual = async () => { await runManualBackup(); message.success("手动备份完成"); reload(); };

  return (
    <div className="page-stack">
      <Typography.Title level={2}>备份恢复</Typography.Title>
      <Card title="备份保留与清理计划" extra={<Space><Button onClick={onTest}>连接测试</Button><Button type="primary" onClick={onManual}>手动备份</Button></Space>}>
        <Descriptions bordered column={1}>
          <Descriptions.Item label="本机保留">{plan?.local_retention_days ?? 7} 天</Descriptions.Item>
          <Descriptions.Item label="gugunas 外部保留">{plan?.external_retention_days ?? 30} 天</Descriptions.Item>
          <Descriptions.Item label="自动清理时间">每天 {plan?.cleanup_time ?? "03:30"}</Descriptions.Item>
          <Descriptions.Item label="外部目录">{plan?.external_target_path ?? "/home/admin/aidp监控平台备份"}</Descriptions.Item>
          <Descriptions.Item label="失败告警">{plan?.cleanup_failure_alert ?? "面板告警 + 飞书告警"}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="备份任务记录"><Table columns={columns} dataSource={jobs} rowKey="id" /></Card>
    </div>
  );
}
