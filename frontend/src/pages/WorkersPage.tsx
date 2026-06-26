import { Alert, Button, Card, Collapse, Descriptions, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { Key } from "react";
import { useEffect, useMemo, useState } from "react";

import {
  approveExecutionDevice,
  checkExecutionDeviceUpdates,
  deleteExecutionDevice,
  disableExecutionDevice,
  downloadLocalAgentInstaller,
  downloadLocalAgentSuite,
  fetchDeletedExecutionDevices,
  fetchExecutionDeviceSummary,
  fetchExecutionDevices,
  fetchLocalAgentLatestRelease,
  pauseExecutionDeviceReceiving,
  rejectExecutionDevice,
  resumeExecutionDeviceReceiving,
  restoreExecutionDevice,
  updateExecutionDeviceCapacity,
  type DeletedExecutionDevice,
  type ExecutionDeviceItem,
  type ExecutionDeviceSummaryResponse,
  type LocalAgentReleaseRead,
} from "../api/client";

function safeError(error: unknown): string {
  return error instanceof Error ? error.message : "接口请求失败";
}

function statusTag(value: string) {
  const color = value === "online" ? "green" : value === "pending_approval" || value === "degraded" ? "gold" : value === "disabled" || value === "rejected" ? "red" : "default";
  const labels: Record<string, string> = { online: "在线", offline: "离线", degraded: "异常", pending_approval: "待批准", disabled: "已禁用", rejected: "已拒绝" };
  return <Tag color={color}>{labels[value] ?? value}</Tag>;
}

function stateTag(value: string) {
  const labels: Record<string, string> = { idle: "空闲", running: "运行中", paused_receiving: "暂停接收任务" };
  return <Tag color={value === "running" ? "blue" : value === "paused_receiving" ? "orange" : "default"}>{labels[value] ?? value}</Tag>;
}

export function WorkersPage() {
  const [items, setItems] = useState<ExecutionDeviceItem[]>([]);
  const [summary, setSummary] = useState<ExecutionDeviceSummaryResponse | null>(null);
  const [deletedItems, setDeletedItems] = useState<DeletedExecutionDevice[]>([]);
  const [release, setRelease] = useState<LocalAgentReleaseRead | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const [loading, setLoading] = useState(false);
  const [recycleVisible, setRecycleVisible] = useState(false);
  const [recycleLoading, setRecycleLoading] = useState(false);
  const [filters, setFilters] = useState({ q: "", status: "", approval_status: "", update_status: "", current_state: "" });

  const load = async () => {
    setLoading(true);
    try {
      const [deviceRows, deviceSummary, latestRelease] = await Promise.all([
        fetchExecutionDevices({ ...filters, page: 1, page_size: 100 }),
        fetchExecutionDeviceSummary(),
        fetchLocalAgentLatestRelease(),
      ]);
      setItems(deviceRows.items);
      setSummary(deviceSummary);
      setRelease(latestRelease);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const visibleItems = useMemo(() => items.filter((item) => {
    const q = filters.q.trim().toLowerCase();
    if (!q) return true;
    return [item.worker_id, item.device_name, item.needs_attention].some((value) => String(value || "").toLowerCase().includes(q));
  }), [items, filters.q]);

  const selectedIds = selectedRowKeys.map(String);

  const loadDeletedDevices = async () => {
    setRecycleLoading(true);
    try {
      setDeletedItems(await fetchDeletedExecutionDevices());
    } catch (error: unknown) {
      message.error(`设备回收站加载失败：${safeError(error)}`);
    } finally {
      setRecycleLoading(false);
    }
  };

  const openRecycleBin = () => {
    setRecycleVisible(true);
    void loadDeletedDevices();
  };

  const runDeviceAction = async (action: () => Promise<unknown>, success: string) => {
    try {
      await action();
      message.success(success);
      await load();
    } catch (error: unknown) {
      message.error(safeError(error));
    }
  };

  const setCapacity = (workerId: string, currentSlots: number) => {
    let nextSlots = Math.max(1, currentSlots || 1);
    Modal.confirm({
      title: "设置并发",
      content: (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text>设备名称：{workerId}</Typography.Text>
          <Typography.Text type="secondary">manual_slots 必须为正整数，低于当前运行并发时只影响后续新任务。</Typography.Text>
          <InputNumber min={1} defaultValue={nextSlots} onChange={(value) => { nextSlots = Number(value ?? 1); }} addonBefore="新的并发上限" />
        </Space>
      ),
      onOk: () => runDeviceAction(() => updateExecutionDeviceCapacity(workerId, nextSlots), "已更新设备并发"),
    });
  };

  const batchCapacity = () => {
    if (!selectedIds.length) {
      message.warning("请先选择设备。");
      return;
    }
    let nextSlots = 1;
    Modal.confirm({
      title: "批量设置并发",
      content: <InputNumber min={1} defaultValue={nextSlots} onChange={(value) => { nextSlots = Number(value ?? 1); }} addonBefore="新的并发上限" />,
      onOk: () => runDeviceAction(async () => { await Promise.all(selectedIds.map((id) => updateExecutionDeviceCapacity(id, nextSlots))); }, "已批量设置并发"),
    });
  };

  const confirmDeleteDevice = (record: ExecutionDeviceItem) => {
    Modal.confirm({
      title: "确认删除该执行设备？",
      content: (
        <Space direction="vertical">
          <Typography.Text>删除后，该设备会从执行设备列表移除，不再接收生产任务。</Typography.Text>
          <Typography.Text>你可以在设备回收站中恢复该设备。</Typography.Text>
          <Typography.Text>历史租约、命令和日志会保留。</Typography.Text>
          <Typography.Text>设备再次上线前，恢复后的状态为离线。</Typography.Text>
        </Space>
      ),
      okText: "确认删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        await runDeviceAction(() => deleteExecutionDevice(record.worker_id), "已移入设备回收站");
        if (recycleVisible) await loadDeletedDevices();
      },
    });
  };

  const confirmRestoreDevice = (record: DeletedExecutionDevice) => {
    Modal.confirm({
      title: "确认恢复该执行设备？",
      content: "恢复后，该设备会重新出现在执行设备列表中；设备再次心跳后可继续上线。",
      okText: "确认恢复",
      cancelText: "取消",
      onOk: async () => {
        await runDeviceAction(() => restoreExecutionDevice(record.worker_id), "已恢复执行设备");
        await loadDeletedDevices();
      },
    });
  };

  const columns: ColumnsType<ExecutionDeviceItem> = [
    {
      title: "设备",
      key: "device",
      fixed: "left",
      width: 210,
      ellipsis: true,
      render: (_, record) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong ellipsis style={{ maxWidth: 180 }}>{record.device_name || record.worker_id}</Typography.Text>
          <Typography.Text type="secondary" ellipsis style={{ maxWidth: 180 }}>{record.worker_id}</Typography.Text>
        </Space>
      ),
    },
    { title: "状态", key: "status", width: 130, render: (_, record) => <Space size={4} wrap>{statusTag(record.status)}{stateTag(record.current_state)}</Space> },
    {
      title: "当前运行",
      key: "current_run",
      width: 150,
      ellipsis: true,
      render: (_, record) => record.current_run?.task_id ? <Typography.Text ellipsis style={{ maxWidth: 130 }}>{`${record.current_run.task_id}${record.current_run.account_user_id ? ` / ${record.current_run.account_user_id}` : ""}`}</Typography.Text> : "空闲",
    },
    { title: "版本", key: "version", width: 120, render: (_, record) => <Space direction="vertical" size={0}><Typography.Text>{record.local_agent_version || "-"}</Typography.Text><Typography.Text type="secondary">{record.extension_version || "-"}</Typography.Text></Space> },
    { title: "并发", key: "capacity", width: 78, render: (_, record) => `${record.running_slots}/${record.manual_slots}` },
    { title: "最近活动", dataIndex: "last_seen_at", key: "last_seen_at", width: 138, render: (value: string | null) => value ? new Date(value).toLocaleString() : "-" },
    { title: "需处理", dataIndex: "needs_attention", key: "needs_attention", width: 140, ellipsis: true, render: (value: string) => value ? <Tag color="orange">{value}</Tag> : <Tag color="green">无需处理</Tag> },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      width: 270,
      render: (_, record) => (
        <Space size={4} wrap>
          <Button size="small" disabled={record.approval_status !== "pending"} onClick={() => runDeviceAction(() => approveExecutionDevice(record.worker_id, 1), "已批准设备")}>批准</Button>
          <Button size="small" disabled={record.approval_status !== "pending"} onClick={() => runDeviceAction(() => rejectExecutionDevice(record.worker_id), "已拒绝设备")}>拒绝</Button>
          <Button size="small" onClick={() => setCapacity(record.worker_id, record.manual_slots)}>并发</Button>
          <Button size="small" onClick={() => runDeviceAction(() => checkExecutionDeviceUpdates(record.worker_id), "已检查更新")}>更新</Button>
          {record.current_state === "paused_receiving" ? (
            <Button size="small" onClick={() => runDeviceAction(() => resumeExecutionDeviceReceiving(record.worker_id), "已恢复接收任务")}>恢复接收</Button>
          ) : (
            <Button size="small" onClick={() => runDeviceAction(() => pauseExecutionDeviceReceiving(record.worker_id), "已暂停接收任务")}>暂停接收</Button>
          )}
          <Button danger size="small" onClick={() => runDeviceAction(() => disableExecutionDevice(record.worker_id), "已禁用设备")}>禁用</Button>
          <Button danger size="small" disabled={record.worker_id === "platform-worker"} onClick={() => confirmDeleteDevice(record)}>删除</Button>
          <Button size="small" onClick={() => message.info("日志入口在高级调试中查看。")}>日志</Button>
        </Space>
      ),
    },
  ];

  const deletedColumns: ColumnsType<DeletedExecutionDevice> = [
    { title: "设备", dataIndex: "device_name", key: "device_name" },
    { title: "Worker ID", dataIndex: "worker_id", key: "worker_id", ellipsis: true },
    { title: "删除时间", dataIndex: "deleted_at", key: "deleted_at", render: (value: string | null) => value ? new Date(value).toLocaleString() : "-" },
    { title: "原因", dataIndex: "delete_reason", key: "delete_reason", render: (value: string) => value || "-" },
    { title: "状态", dataIndex: "status_label", key: "status_label", render: (value: string) => <Tag>{value}</Tag> },
    { title: "操作", key: "actions", render: (_, record) => <Button size="small" onClick={() => confirmRestoreDevice(record)}>恢复</Button> },
  ];

  return (
    <div className="page-stack">
      <Space align="center" wrap>
        <Typography.Title level={2} style={{ margin: 0 }}>执行设备管理</Typography.Title>
        <Button onClick={() => void load()} loading={loading}>刷新</Button>
        <Button onClick={openRecycleBin}>设备回收站</Button>
      </Space>

      <Card title="本机助手套件">
        <Descriptions bordered size="small" column={3}>
          <Descriptions.Item label="套件名称">{release?.suite_name ?? "aidp-local-suite-0.9.1.zip"}</Descriptions.Item>
          <Descriptions.Item label="推荐版本">{release?.version ?? "0.9.1"}</Descriptions.Item>
          <Descriptions.Item label="本机助手版本">{release?.windows_launcher?.version ?? release?.version ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="安装包版本">{release?.windows_installer?.version ?? release?.version ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="插件版本">{release?.version ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="平台地址">/api/v1</Descriptions.Item>
          <Descriptions.Item label="连接状态"><Tag color="green">已连接</Tag></Descriptions.Item>
          <Descriptions.Item label="更新状态"><Tag color="blue">已最新</Tag></Descriptions.Item>
        </Descriptions>
        <Space wrap style={{ marginTop: 16 }}>
          <Button type="primary" onClick={downloadLocalAgentInstaller}>下载安装包</Button>
          <Button type="primary" onClick={downloadLocalAgentSuite}>下载套件</Button>
          <Button href="/docs/local-agent" target="_blank">安装说明</Button>
          <Button onClick={() => selectedIds.length ? void runDeviceAction(async () => { await Promise.all(selectedIds.map(checkExecutionDeviceUpdates)); }, "已批量检查更新") : void load()}>检查更新</Button>
        </Space>
      </Card>

      <Card title="设备总览">
        <Space wrap>
          <Tag>全部设备 {summary?.total ?? 0}</Tag>
          <Tag color="green">在线 {summary?.online ?? 0}</Tag>
          <Tag color="blue">运行中 {summary?.running ?? 0}</Tag>
          <Tag color="gold">待批准 {summary?.pending_approval ?? 0}</Tag>
          <Tag color="red">异常 {summary?.abnormal ?? 0}</Tag>
          <Tag color="orange">需更新 {summary?.update_needed ?? 0}</Tag>
        </Space>
      </Card>

      <Alert type="warning" showIcon message="需要处理提示条" description={`${visibleItems.filter((item) => item.needs_attention).length} 台设备需要处理；暂停或禁用的设备不会进入生产自动分配。`} />

      <Card title="搜索与筛选">
        <Space wrap>
          <Input.Search style={{ width: 260 }} allowClear placeholder="搜索设备名 / IP / 备注" value={filters.q} onChange={(event) => setFilters((current) => ({ ...current, q: event.target.value }))} onSearch={() => void load()} />
          <Select style={{ width: 150 }} value={filters.status} onChange={(value) => setFilters((current) => ({ ...current, status: value }))} options={[{ value: "", label: "状态：全部" }, { value: "online", label: "在线" }, { value: "offline", label: "离线" }, { value: "degraded", label: "异常" }]} />
          <Select style={{ width: 150 }} value={filters.approval_status} onChange={(value) => setFilters((current) => ({ ...current, approval_status: value }))} options={[{ value: "", label: "批准：全部" }, { value: "approved", label: "已批准" }, { value: "pending", label: "待批准" }, { value: "disabled", label: "已禁用" }]} />
          <Select style={{ width: 150 }} value={filters.update_status} onChange={(value) => setFilters((current) => ({ ...current, update_status: value }))} options={[{ value: "", label: "更新：全部" }, { value: "update_available", label: "有更新" }, { value: "latest", label: "已最新" }, { value: "waiting_idle", label: "等待空闲" }]} />
          <Select style={{ width: 170 }} value={filters.current_state} onChange={(value) => setFilters((current) => ({ ...current, current_state: value }))} options={[{ value: "", label: "运行：全部" }, { value: "idle", label: "空闲" }, { value: "running", label: "运行中" }, { value: "paused_receiving", label: "暂停接收任务" }]} />
          <Button onClick={() => void load()}>应用筛选</Button>
        </Space>
        <Space wrap style={{ marginTop: 16 }}>
          <Button disabled={!selectedIds.length} onClick={() => runDeviceAction(async () => { await Promise.all(selectedIds.map(checkExecutionDeviceUpdates)); }, "已批量检查更新")}>批量检查更新</Button>
          <Button disabled={!selectedIds.length} onClick={() => runDeviceAction(async () => { await Promise.all(selectedIds.map(pauseExecutionDeviceReceiving)); }, "已批量暂停接收任务")}>批量暂停接收任务</Button>
          <Button disabled={!selectedIds.length} onClick={() => runDeviceAction(async () => { await Promise.all(selectedIds.map(resumeExecutionDeviceReceiving)); }, "已批量恢复接收任务")}>批量恢复接收任务</Button>
          <Button disabled={!selectedIds.length} onClick={batchCapacity}>批量设置并发</Button>
        </Space>
      </Card>

      <Card title="设备列表表格">
        <Table
          size="small"
          rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
          columns={columns}
          dataSource={visibleItems}
          rowKey="worker_id"
          loading={loading}
          scroll={{ x: 1240 }}
          pagination={{ defaultPageSize: 20, pageSizeOptions: [20, 50, 100], showSizeChanger: true }}
        />
      </Card>

      <Collapse
        defaultActiveKey={[]}
        items={[
          {
            key: "advanced-debug",
            label: "高级调试",
            children: (
              <Space direction="vertical" style={{ width: "100%" }}>
                <Alert type="info" showIcon message="命令详情、租约详情、心跳时间、回执记录、错误码、重派记录仅在这里展示，不进入主界面。" />
                <pre className="pre-wrap">{JSON.stringify(items, null, 2)}</pre>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="设备回收站"
        open={recycleVisible}
        width={860}
        footer={[<Button key="close" onClick={() => setRecycleVisible(false)}>关闭</Button>]}
        onCancel={() => setRecycleVisible(false)}
      >
        <Table columns={deletedColumns} dataSource={deletedItems} rowKey="worker_id" loading={recycleLoading} pagination={false} size="small" />
      </Modal>
    </div>
  );
}
