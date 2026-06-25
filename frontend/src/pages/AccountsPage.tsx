import { Alert, Button, Card, Descriptions, Empty, Form, Input, Modal, Space, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { useEffect, useRef, useState } from "react";

import {
  createAccountReloginSlot,
  createNewAccountLoginSlot,
  deleteProductionAccount,
  fetchAccountLoginSlots,
  fetchDeletedProductionAccounts,
  fetchProductionDashboard,
  fetchProductionRefreshStatus,
  openAccountTarget,
  refreshAccountUsernames,
  refreshProductionAccount,
  refreshProductionAccounts,
  restoreProductionAccount,
  updateAccountMetadata,
  type AccountLoginSlot,
  type DeletedProductionAccount,
  type ProductionAutoRefreshStatus,
  type ProductionAccountCard,
  type ProductionDashboardSummary,
} from "../api/client";

const statusColor: Record<string, string> = {
  active: "green",
  stale: "gold",
  needs_login: "red",
  disabled: "default",
  pending: "gold",
  warning: "orange",
};

function safeError(error: unknown): string {
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { data?: { detail?: string; message?: string } } }).response;
    return response?.data?.detail || response?.data?.message || "接口请求失败";
  }
  return error instanceof Error ? error.message : "接口请求失败";
}

function money(value: string | number | undefined): string {
  const numeric = Number(String(value ?? "0").replace(/,/g, ""));
  return Number.isFinite(numeric) ? numeric.toFixed(2) : String(value ?? "0.00");
}

function formatTime(value: string | null | undefined): string {
  return value ? dayjs(value).format("MM-DD HH:mm") : "未刷新";
}

export function AccountsPage() {
  const [dashboard, setDashboard] = useState<ProductionDashboardSummary | null>(null);
  const [autoRefresh, setAutoRefresh] = useState<ProductionAutoRefreshStatus | null>(null);
  const [loginSlots, setLoginSlots] = useState<AccountLoginSlot[]>([]);
  const [currentSlot, setCurrentSlot] = useState<AccountLoginSlot | null>(null);
  const [editingAccount, setEditingAccount] = useState<ProductionAccountCard | null>(null);
  const [deletedAccounts, setDeletedAccounts] = useState<DeletedProductionAccount[]>([]);
  const [recycleVisible, setRecycleVisible] = useState(false);
  const [recycleLoading, setRecycleLoading] = useState(false);
  const [recycleError, setRecycleError] = useState("");
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState("");
  const [metadataForm] = Form.useForm<{ custom_name: string; note: string }>();
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const reloadInFlight = useRef(false);

  const reload = async (silent = false) => {
    if (reloadInFlight.current) return;
    reloadInFlight.current = true;
    if (!silent) setLoading(true);
    try {
      const [productionResult, slots, autoRefreshResult] = await Promise.all([
        fetchProductionDashboard(),
        fetchAccountLoginSlots(),
        fetchProductionRefreshStatus().catch(() => null),
      ]);
      setDashboard(productionResult);
      setLoginSlots(slots);
      setAutoRefresh(autoRefreshResult);
      setLastUpdatedAt(dayjs().format("HH:mm:ss"));
      setRefreshError("");
    } catch (error: unknown) {
      if (!silent) message.error(safeError(error));
      if (silent) setRefreshError(safeError(error));
    } finally {
      reloadInFlight.current = false;
      if (!silent) setLoading(false);
    }
  };

  useEffect(() => { void reload(); }, []);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void reload(true);
    }, 60000);
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") void reload(true);
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  const openLocalUrl = (url: string) => {
    window.open(url, "_blank", "noopener,noreferrer");
  };

  const onNewLogin = async () => {
    setActionLoading("new-login");
    try {
      const slot = await createNewAccountLoginSlot();
      setCurrentSlot(slot);
      await reload();
      message.success("已创建新账号登录入口；同步到真实 userId 后才会进入账号列表");
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const onRelogin = async (userId: string) => {
    setActionLoading(`relogin-${userId}`);
    try {
      const slot = await createAccountReloginSlot(userId);
      setCurrentSlot(slot);
      await reload();
      message.success("已创建重新登录入口");
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const onOpenTarget = async (userId: string, target: "task" | "personal") => {
    const popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    setActionLoading(`${target}-${userId}`);
    try {
      const result = await openAccountTarget(userId, target);
      if (popup) {
        popup.location.replace(result.open_url);
      } else {
        openLocalUrl(result.open_url);
      }
      message.success(result.message);
    } catch (error: unknown) {
      popup?.close();
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const onRefreshProduction = async () => {
    setActionLoading("refresh-production");
    try {
      const result = await refreshProductionAccounts();
      if (result.failed_count) {
        message.warning(result.message || `账号数据刷新完成：成功 ${result.refreshed_count} 个，失败 ${result.failed_count} 个`);
      } else {
        message.success(result.message);
      }
      await reload();
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const onRefreshOneProduction = async (userId: string) => {
    setActionLoading(`refresh-production-${userId}`);
    try {
      const result = await refreshProductionAccount(userId);
      if (result.failed_count) {
        message.warning(result.message || `账号数据刷新完成：成功 ${result.refreshed_count} 个，失败 ${result.failed_count} 个`);
      } else {
        message.success(result.message);
      }
      await reload();
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const onRefreshUsernames = async () => {
    setActionLoading("refresh-usernames");
    try {
      const result = await refreshAccountUsernames();
      const failed = result.items.filter((item) => item.error);
      if (failed.length) {
        message.warning(`真实用户名刷新完成：更新 ${result.updated_count} 个，失败 ${failed.length} 个`);
      } else {
        message.success(result.message);
      }
      await reload();
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const openMetadataEditor = (record: ProductionAccountCard) => {
    setEditingAccount(record);
    metadataForm.setFieldsValue({ custom_name: record.custom_name || "", note: record.note || "" });
  };

  const onSaveMetadata = async () => {
    if (!editingAccount) return;
    setActionLoading(`metadata-${editingAccount.user_id}`);
    try {
      const values = await metadataForm.validateFields();
      const result = await updateAccountMetadata(editingAccount.user_id, values);
      message.success(result.message);
      setEditingAccount(null);
      await reload();
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const loadDeletedAccounts = async () => {
    setRecycleLoading(true);
    setRecycleError("");
    try {
      setDeletedAccounts(await fetchDeletedProductionAccounts());
    } catch {
      setRecycleError("账号回收站加载失败，请稍后重试。");
    } finally {
      setRecycleLoading(false);
    }
  };

  const openRecycleBin = () => {
    setRecycleVisible(true);
    void loadDeletedAccounts();
  };

  const onDeleteAccount = (record: ProductionAccountCard) => {
    Modal.confirm({
      title: "确认删除该账号？",
      content: (
        <Space direction="vertical">
          <Typography.Text>删除后，该账号将从真实账号列表移除，不再用于数据刷新、任务同步、试运行和生产运行。</Typography.Text>
          <Typography.Text>你可以在账号回收站中恢复该账号。</Typography.Text>
          <Typography.Text>历史任务记录、运行日志和错误日志会保留。</Typography.Text>
          <Typography.Text>本机浏览器 Profile 不会自动删除。</Typography.Text>
        </Space>
      ),
      okText: "确认删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      async onOk() {
        setActionLoading(`delete-${record.user_id}`);
        try {
          const result = await deleteProductionAccount(record.user_id);
          message.success(result.message);
          await reload(true);
          if (recycleVisible) await loadDeletedAccounts();
        } catch (error: unknown) {
          message.error(`删除失败：${safeError(error)}`);
          throw error;
        } finally {
          setActionLoading(null);
        }
      },
    });
  };

  const onRestoreAccount = (record: DeletedProductionAccount) => {
    Modal.confirm({
      title: "确认恢复该账号？",
      content: "恢复后，该账号会重新出现在真实账号列表中，并可参与数据刷新。",
      okText: "确认恢复",
      cancelText: "取消",
      async onOk() {
        setActionLoading(`restore-${record.user_id}`);
        try {
          const result = await restoreProductionAccount(record.user_id);
          message.success(result.message);
          await Promise.all([loadDeletedAccounts(), reload(true)]);
        } catch (error: unknown) {
          message.error(`恢复失败：${safeError(error)}`);
          throw error;
        } finally {
          setActionLoading(null);
        }
      },
    });
  };

  const accountColumns: ColumnsType<ProductionAccountCard> = [
    {
      title: "账号",
      dataIndex: "display_name",
      key: "display_name",
      render: (_, record) => (
        <Space direction="vertical" size={0}>
          <Space><Typography.Text strong>{record.display_name}</Typography.Text>{record.real_name_ok ? <Tag color="green">真实用户名</Tag> : <Tag color="gold">待同步真实用户名</Tag>}</Space>
          {record.custom_name ? <Typography.Text>自定义账号名：{record.custom_name}</Typography.Text> : null}
          {record.note ? <Typography.Text type="secondary">备注：{record.note}</Typography.Text> : null}
          <Typography.Text type="secondary">{record.user_id}</Typography.Text>
        </Space>
      ),
    },
    { title: "状态", dataIndex: "status_label", key: "status_label", render: (value: string, record) => <Tag color={statusColor[record.status] ?? "blue"}>{value}</Tag> },
    { title: "个人中心金额", key: "income", render: (_, record) => `总 ${money(record.total_income)} / 本月 ${money(record.current_month_income)} / 可提现 ${money(record.withdrawable_amount)}` },
    { title: "任务统计", key: "tasks", render: (_, record) => `任务 ${record.task_count} / 交付 ${record.delivered_total} / 废弃 ${record.abandoned_total} / 处理中 ${record.processing_total} / 进行中 ${record.in_progress_total} / 待处理 ${record.pending_total}` },
    { title: "最近刷新", dataIndex: "last_refresh_at", key: "last_refresh_at", render: formatTime },
    { title: "提示", dataIndex: "warning", key: "warning", render: (value: string, record) => value ? <Typography.Text type="warning">{value || record.stale_reason}</Typography.Text> : <Typography.Text type="secondary">正常</Typography.Text> },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      render: (_, record) => (
        <Space wrap>
          <Button size="small" type="primary" loading={actionLoading === `task-${record.user_id}`} onClick={() => void onOpenTarget(record.user_id, "task")}>任务页</Button>
          <Button size="small" loading={actionLoading === `personal-${record.user_id}`} onClick={() => void onOpenTarget(record.user_id, "personal")}>个人中心</Button>
          <Button size="small" loading={actionLoading === `refresh-production-${record.user_id}`} onClick={() => void onRefreshOneProduction(record.user_id)}>刷新数据</Button>
          <Button size="small" loading={actionLoading === `metadata-${record.user_id}`} onClick={() => openMetadataEditor(record)}>编辑名称/备注</Button>
          <Button size="small" loading={actionLoading === `relogin-${record.user_id}`} onClick={() => void onRelogin(record.user_id)}>重新登录</Button>
          <Button size="small" danger loading={actionLoading === `delete-${record.user_id}`} onClick={() => onDeleteAccount(record)}>删除</Button>
        </Space>
      ),
    },
  ];

  const deletedColumns: ColumnsType<DeletedProductionAccount> = [
    { title: "账号名称", dataIndex: "display_name", key: "display_name" },
    { title: "账号 ID", dataIndex: "user_id", key: "user_id" },
    { title: "删除时间", dataIndex: "deleted_at", key: "deleted_at", render: formatTime },
    { title: "状态", dataIndex: "status_label", key: "status_label", render: (value: string, record) => <Space><Tag color="default">{value}</Tag>{record.cookie_preserved ? <Tag color="green">Cookie 已保留</Tag> : null}{record.profile_preserved ? <Tag color="blue">Profile 已保留</Tag> : null}</Space> },
    { title: "操作", key: "actions", render: (_, record) => <Button size="small" loading={actionLoading === `restore-${record.user_id}`} onClick={() => onRestoreAccount(record)}>恢复</Button> },
  ];

  const slotColumns: ColumnsType<AccountLoginSlot> = [
    { title: "登录会话", dataIndex: "login_session_id", key: "login_session_id", ellipsis: true },
    { title: "账号", dataIndex: "display_name", key: "display_name" },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "gold"}>{value}</Tag> },
    { title: "CDP 端口", dataIndex: "cdp_port", key: "cdp_port" },
    { title: "说明", key: "note", render: (_, record) => record.pending_login ? "等待登录并同步真实用户名" : "已绑定真实账号" },
    { title: "操作", key: "actions", render: (_, record) => <Button size="small" onClick={() => setCurrentSlot(record)}>查看/打开</Button> },
  ];

  return (
    <div className="page-stack">
      <Space align="start" className="page-heading">
        <div>
          <Typography.Title level={2} style={{ marginBottom: 4 }}>统一账号管理</Typography.Title>
          <Typography.Text type="secondary">这里不区分旧版/新版；只管理真实 AIDP 账号。未登录会话只在下方暂存，不进入生产统计。</Typography.Text>
        </div>
        <Space wrap>
          <Button onClick={() => void reload()} loading={loading}>刷新账号</Button>
          <Button type="primary" loading={actionLoading === "refresh-production"} onClick={() => void onRefreshProduction()}>刷新全部账号数据</Button>
          <Button type="primary" loading={actionLoading === "new-login"} onClick={() => void onNewLogin()}>登录新账号</Button>
          <Button loading={actionLoading === "refresh-usernames"} onClick={() => void onRefreshUsernames()}>刷新真实用户名</Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="账号列表只显示真实 userId"
        description={`用户名优先使用个人中心/AIDP 用户信息接口返回的“用户+数字”。后台自刷新：${autoRefresh?.enabled ? `已开启，每 ${Math.round((autoRefresh.interval_seconds || 0) / 60)} 分钟` : "未开启"}；页面每 60 秒静默刷新，切回前台时刷新。${lastUpdatedAt ? `最后更新：${lastUpdatedAt}` : ""}`}
      />

      {autoRefresh?.last_error ? <Alert type="warning" showIcon message="后台自刷新最近一次失败" description={autoRefresh.last_error} /> : null}
      {refreshError ? <Alert type="warning" showIcon message="页面静默刷新失败，已保留旧数据" description={refreshError} /> : null}

      <Card
        title="真实账号列表"
        extra={<Space wrap><Button onClick={openRecycleBin}>账号回收站</Button><Typography.Text type="secondary">任务页、个人中心、重新登录按钮必须保留</Typography.Text></Space>}
      >
        {dashboard?.accounts.length ? <Table columns={accountColumns} dataSource={dashboard.accounts} rowKey="user_id" loading={loading} scroll={{ x: 1250 }} /> : <Empty description="暂无真实账号；请先登录并同步个人中心" />}
      </Card>

      <Modal
        title="账号回收站"
        open={recycleVisible}
        width={860}
        footer={[<Button key="close" onClick={() => setRecycleVisible(false)}>关闭</Button>]}
        onCancel={() => setRecycleVisible(false)}
      >
        {recycleError ? <Alert type="warning" showIcon message={recycleError} style={{ marginBottom: 16 }} /> : null}
        <Table columns={deletedColumns} dataSource={deletedAccounts} rowKey="user_id" loading={recycleLoading} pagination={false} size="small" />
      </Modal>

      <Modal
        title="编辑自定义账号名和备注"
        open={Boolean(editingAccount)}
        onOk={() => void onSaveMetadata()}
        confirmLoading={editingAccount ? actionLoading === `metadata-${editingAccount.user_id}` : false}
        onCancel={() => setEditingAccount(null)}
        okText="保存"
        cancelText="取消"
      >
        <Alert type="info" showIcon message="真实用户名不会被覆盖" description="自定义账号名和备注只用于值守识别；个人中心同步回来的“用户+数字”仍保留为真实用户名。" />
        <Form form={metadataForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item label="真实用户名">
            <Input value={editingAccount?.display_name ?? ""} disabled />
          </Form.Item>
          <Form.Item label="自定义账号名" name="custom_name">
            <Input maxLength={128} placeholder="例如：一号做题号、备用评分号" />
          </Form.Item>
          <Form.Item label="备注" name="note">
            <Input.TextArea maxLength={500} rows={4} placeholder="例如：主跑评分题、需要晚间复查、备用账号" />
          </Form.Item>
        </Form>
      </Modal>

      <Card title="待登录会话" extra={<Typography.Text type="secondary">临时会话不参与收益/题量统计</Typography.Text>}>
        <Table columns={slotColumns} dataSource={loginSlots} rowKey="login_session_id" pagination={false} size="small" />
      </Card>

      <Modal
        title="本机登录助手"
        open={Boolean(currentSlot)}
        width={760}
        onCancel={() => setCurrentSlot(null)}
        footer={currentSlot ? [
          <Button key="open" type="primary" onClick={() => openLocalUrl(currentSlot.open_profile_url)}>打开登录窗口</Button>,
          <Button key="sync" onClick={() => openLocalUrl(currentSlot.sync_url)}>同步登录态</Button>,
          <Button key="close" onClick={() => setCurrentSlot(null)}>关闭</Button>,
        ] : null}
      >
        {currentSlot ? (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert type="warning" showIcon message="先启动本机助手，再打开登录窗口" description="完成登录后请进入 AIDP 工作台或个人中心，再点击同步登录态；未识别真实 userId/用户名时后端会拒绝或标记待同步。" />
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="启动命令"><Typography.Paragraph copyable style={{ marginBottom: 0 }}>{currentSlot.launcher_start_command}</Typography.Paragraph></Descriptions.Item>
              <Descriptions.Item label="打开登录窗口"><Typography.Paragraph copyable style={{ marginBottom: 0 }}>{currentSlot.open_profile_url}</Typography.Paragraph></Descriptions.Item>
              <Descriptions.Item label="同步登录态"><Typography.Paragraph copyable style={{ marginBottom: 0 }}>{currentSlot.sync_url}</Typography.Paragraph></Descriptions.Item>
              <Descriptions.Item label="监控地址">{currentSlot.monitor_url}</Descriptions.Item>
            </Descriptions>
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {currentSlot.instructions.map((item) => <li key={item}>{item}</li>)}
            </ul>
          </Space>
        ) : null}
      </Modal>
    </div>
  );
}
