import {
  Alert,
  Button,
  Card,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
  type TableColumnsType,
} from "antd";
import { LoginOutlined, ProfileOutlined, ReloadOutlined, SyncOutlined, UserOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createNewAccountLoginSlot,
  fetchAiTimerSummary,
  fetchProductionDashboard,
  fetchProductionRefreshStatus,
  openAccountTarget,
  refreshProductionAccount,
  refreshProductionAccounts,
  type AiTimerSummaryResponse,
  type ProductionAccountCard,
  type ProductionAutoRefreshStatus,
  type ProductionDashboardSummary,
} from "../api/client";

const statusColor: Record<string, string> = {
  active: "green",
  stale: "gold",
  needs_login: "red",
  disabled: "default",
};

function parseMoney(value: string | number | undefined): number {
  const numeric = Number(String(value ?? "0").replace(/,/g, ""));
  return Number.isFinite(numeric) ? numeric : 0;
}

function money(value: string | number | undefined): string {
  return parseMoney(value).toFixed(2);
}

function formatTime(value: string | null | undefined): string {
  return value ? dayjs(value).format("MM-DD HH:mm") : "未刷新";
}

function durationSeconds(value: number | undefined): string {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? (numeric / 1000).toFixed(2) : "0.00";
}

function sumAccountPending(account: ProductionAccountCard): number {
  return account.task_stats.reduce((total, task) => total + task.pending, 0);
}

function hasAccountIssue(account: ProductionAccountCard): boolean {
  return account.status !== "active" || Boolean(account.warning) || account.data_stale || !account.cookie_synced;
}

function issueText(account: ProductionAccountCard): string {
  if (account.warning) return account.warning;
  if (account.data_stale) return account.stale_reason || "数据过旧";
  if (!account.cookie_synced) return "待重新登录";
  if (account.status !== "active") return account.status_label;
  return "-";
}

function safeError(error: unknown): string {
  return error instanceof Error ? error.message : "接口请求失败";
}

export function DashboardPage() {
  const [dashboard, setDashboard] = useState<ProductionDashboardSummary | null>(null);
  const [autoRefresh, setAutoRefresh] = useState<ProductionAutoRefreshStatus | null>(null);
  const [aiTimer, setAiTimer] = useState<AiTimerSummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [productionResult, autoRefreshResult, aiTimerResult] = await Promise.all([
        fetchProductionDashboard(),
        fetchProductionRefreshStatus().catch(() => null),
        fetchAiTimerSummary().catch(() => null),
      ]);
      setDashboard(productionResult);
      setAutoRefresh(autoRefreshResult);
      setAiTimer(aiTimerResult);
    } catch (error: unknown) {
      if (!silent) message.error(safeError(error));
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, 30000);
    return () => window.clearInterval(timer);
  }, [load]);

  const accounts = dashboard?.accounts ?? [];
  const activeAccounts = dashboard?.active_account_count ?? 0;
  const issueAccounts = useMemo(() => accounts.filter(hasAccountIssue), [accounts]);
  const sortedAccounts = useMemo(() => {
    return [...accounts].sort((left, right) => {
      const leftIssue = hasAccountIssue(left) ? 1 : 0;
      const rightIssue = hasAccountIssue(right) ? 1 : 0;
      const leftPending = sumAccountPending(left);
      const rightPending = sumAccountPending(right);
      return rightIssue - leftIssue || rightPending - leftPending || right.processing_total - left.processing_total || right.in_progress_total - left.in_progress_total || right.task_count - left.task_count;
    });
  }, [accounts]);
  const monthIncome = useMemo(() => accounts.reduce((total, account) => total + parseMoney(account.current_month_income), 0), [accounts]);
  const withdrawableAmount = useMemo(() => accounts.reduce((total, account) => total + parseMoney(account.withdrawable_amount), 0), [accounts]);

  const openTarget = async (account: ProductionAccountCard, target: "task" | "personal") => {
    setActionLoading(`${target}-${account.user_id}`);
    try {
      const result = await openAccountTarget(account.user_id, target);
      window.open(result.open_url, "_blank", "noopener,noreferrer");
      message.success(result.message);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const startNewLogin = async () => {
    setActionLoading("new-login");
    try {
      const slot = await createNewAccountLoginSlot();
      window.open(slot.open_profile_url, "_blank", "noopener,noreferrer");
      message.success("已打开新账号登录窗口");
      await load();
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const refreshProduction = async () => {
    setActionLoading("refresh-production");
    try {
      const result = await refreshProductionAccounts();
      const feedback = result.failed_count > 0 ? message.warning : message.success;
      feedback(result.message || `刷新完成：成功 ${result.refreshed_count} 个，失败 ${result.failed_count} 个`);
      await load();
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const refreshOneAccount = async (account: ProductionAccountCard) => {
    setActionLoading(`refresh-production-${account.user_id}`);
    try {
      const result = await refreshProductionAccount(account.user_id);
      if (result.failed_count) {
        message.warning(result.message || `账号刷新完成：成功 ${result.refreshed_count} 个，失败 ${result.failed_count} 个`);
      } else {
        message.success(result.message);
      }
      await load();
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setActionLoading(null);
    }
  };

  const columns: TableColumnsType<ProductionAccountCard> = [
    {
      title: "账号",
      key: "account",
      fixed: "left",
      width: 220,
      render: (_, account) => (
        <div className="account-cell">
          <Typography.Text strong ellipsis>{account.custom_name || account.display_name}</Typography.Text>
          <Typography.Text type="secondary" ellipsis>{account.user_id}</Typography.Text>
        </div>
      ),
    },
    {
      title: "状态",
      key: "status",
      width: 150,
      render: (_, account) => (
        <Space size={4} wrap>
          <Tag color={statusColor[account.status] ?? "blue"}>{account.status_label}</Tag>
          {account.cookie_synced ? <Tag color="green">Cookie</Tag> : <Tag color="red">登录</Tag>}
          {account.data_stale ? <Tag color="gold">旧</Tag> : null}
        </Space>
      ),
    },
    {
      title: "收益",
      key: "income",
      width: 190,
      render: (_, account) => (
        <div className="metric-inline">
          <span>本月收入 {money(account.current_month_income)}</span>
          <span>总收入 {money(account.total_income)}</span>
          <span>可提现 {money(account.withdrawable_amount)}</span>
        </div>
      ),
    },
    {
      title: "题量",
      key: "tasks",
      width: 170,
      render: (_, account) => (
        <div className="metric-inline">
          <span>待处理 {sumAccountPending(account)}</span>
          <span>处理中 {account.processing_total}</span>
          <span>进行中 {account.in_progress_total}</span>
          <span>已交付 {account.delivered_total}</span>
        </div>
      ),
    },
    { title: "任务", dataIndex: "task_count", key: "task_count", width: 80 },
    {
      title: "最近刷新",
      dataIndex: "last_refresh_at",
      key: "last_refresh_at",
      width: 120,
      render: (value: string | null) => formatTime(value),
    },
    {
      title: "异常",
      key: "issue",
      width: 220,
      render: (_, account) => <Typography.Text type={hasAccountIssue(account) ? "warning" : "secondary"}>{issueText(account)}</Typography.Text>,
    },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      width: 260,
      render: (_, account) => (
        <Space size={4} wrap>
          <Tooltip title="打开任务页">
            <Button size="small" icon={<ProfileOutlined />} loading={actionLoading === `task-${account.user_id}`} onClick={() => void openTarget(account, "task")}>任务页</Button>
          </Tooltip>
          <Tooltip title="打开个人中心">
            <Button size="small" icon={<UserOutlined />} loading={actionLoading === `personal-${account.user_id}`} onClick={() => void openTarget(account, "personal")}>个人中心</Button>
          </Tooltip>
          <Tooltip title="刷新该账号">
            <Button size="small" icon={<ReloadOutlined />} loading={actionLoading === `refresh-production-${account.user_id}`} onClick={() => void refreshOneAccount(account)}>刷新</Button>
          </Tooltip>
          <Tooltip title="重新登录">
            <Button size="small" href={account.relogin_open_url || undefined} target="_blank" icon={<LoginOutlined />}>重新登录</Button>
          </Tooltip>
        </Space>
      ),
    },
  ];

  const hasAiSamples = (aiTimer?.total_items ?? 0) > 0;

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <Typography.Title level={2} style={{ marginBottom: 4 }}>生产驾驶舱</Typography.Title>
          <Typography.Text type="secondary">更新 {formatTime(dashboard?.generated_at)}</Typography.Text>
        </div>
        <div className="production-command-bar">
          <Button type="primary" icon={<SyncOutlined />} loading={actionLoading === "refresh-production"} onClick={() => void refreshProduction()}>刷新生产数据</Button>
          <Button icon={<LoginOutlined />} loading={actionLoading === "new-login"} onClick={() => void startNewLogin()}>登录新账号</Button>
        </div>
      </div>

      <Card title="生产总览" className="production-summary-card">
        <div className="production-summary-grid">
          <Statistic title="已登录账号" value={activeAccounts} loading={loading} />
          <Statistic title="待处理" value={dashboard?.pending_total ?? 0} loading={loading} />
          <Statistic title="处理中" value={dashboard?.processing_total ?? 0} loading={loading} />
          <Statistic title="进行中" value={dashboard?.in_progress_total ?? 0} loading={loading} />
          <Statistic title="本月收入" value={money(monthIncome)} suffix="元" loading={loading} />
          <Statistic title="可提现" value={money(withdrawableAmount)} suffix="元" loading={loading} />
          <Statistic title="异常账号" value={issueAccounts.length} loading={loading} />
        </div>
      </Card>

      {(issueAccounts.length > 0 || dashboard?.global_stale || autoRefresh?.last_error) ? (
        <Alert
          className="production-alert-strip"
          type="warning"
          showIcon
          message={`异常账号 ${issueAccounts.length} 个${dashboard?.global_stale ? " / 数据过旧" : ""}${autoRefresh?.last_error ? " / 后台刷新失败" : ""}`}
          description={issueAccounts.slice(0, 4).map((account) => `${account.custom_name || account.display_name}: ${issueText(account)}`).join("；") || dashboard?.global_warning || autoRefresh?.last_error}
        />
      ) : null}

      <Card title="AI效率">
        <div className="ai-efficiency-strip">
          <div className="ai-efficiency-item">
            <span>样本</span>
            <strong>{aiTimer?.total_items ?? 0}</strong>
          </div>
          <div className="ai-efficiency-item">
            <span>平均耗时</span>
            <strong>{hasAiSamples ? `${durationSeconds(aiTimer?.avg_total_ms)} 秒` : "暂无样本"}</strong>
          </div>
          <div className="ai-efficiency-item">
            <span>每小时题量</span>
            <strong>{hasAiSamples ? aiTimer?.questions_per_hour.toFixed(2) : "暂无样本"}</strong>
          </div>
          <div className="ai-efficiency-item">
            <span>每小时预计收入</span>
            <strong>{hasAiSamples ? `${money(aiTimer?.estimated_hourly_income ?? 0)} 元` : "暂无样本"}</strong>
          </div>
          <div className="ai-efficiency-item">
            <span>最慢阶段</span>
            <strong>{hasAiSamples ? aiTimer?.slowest_stage.stage : "暂无样本"}</strong>
          </div>
        </div>
      </Card>

      <Card title="账号生产表" className="account-production-table">
        <Table
          rowKey="user_id"
          loading={loading}
          columns={columns}
          dataSource={sortedAccounts}
          size="middle"
          scroll={{ x: 1420 }}
          pagination={{ pageSize: 10, showSizeChanger: true }}
        />
      </Card>
    </div>
  );
}
