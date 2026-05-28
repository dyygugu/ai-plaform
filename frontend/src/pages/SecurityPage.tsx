import { Card, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import { fetchAuditLogs, fetchPermissionMatrix, type AuditLogItem, type PermissionMatrixResponse } from "../api/client";

const auditColumns: ColumnsType<AuditLogItem> = [
  { title: "事件", dataIndex: "event_type", key: "event_type" },
  { title: "级别", dataIndex: "severity", key: "severity", render: (value: string) => <Tag>{value}</Tag> },
  { title: "对象", dataIndex: "target_id", key: "target_id" },
  { title: "说明", dataIndex: "message", key: "message" },
  { title: "trace_id", dataIndex: "trace_id", key: "trace_id" },
];

export function SecurityPage() {
  const [matrix, setMatrix] = useState<PermissionMatrixResponse | null>(null);
  const [logs, setLogs] = useState<AuditLogItem[]>([]);

  useEffect(() => {
    fetchPermissionMatrix().then(setMatrix);
    fetchAuditLogs().then(setLogs);
  }, []);

  const permissionRows = Object.entries(matrix?.roles ?? {}).map(([role, permissions]) => ({ role, ...permissions }));
  const permissionKeys = Array.from(new Set(permissionRows.flatMap((row) => Object.keys(row).filter((key) => key !== "role"))));
  const permissionColumns = [
    { title: "角色", dataIndex: "role", key: "role" },
    ...permissionKeys.map((key) => ({ title: key, dataIndex: key, key, render: (value: boolean) => (value ? <Tag color="green">允许</Tag> : <Tag>禁止</Tag>) })),
  ];

  return (
    <div className="page-stack">
      <Typography.Title level={2}>权限审计</Typography.Title>
      <Card title="权限矩阵">
        <Table columns={permissionColumns} dataSource={permissionRows} rowKey="role" pagination={false} scroll={{ x: true }} />
      </Card>
      <Card title="审计日志">
        <Table columns={auditColumns} dataSource={logs} rowKey="id" />
      </Card>
    </div>
  );
}
