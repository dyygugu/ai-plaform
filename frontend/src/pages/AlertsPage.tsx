import { Alert, Button, Card, Col, Descriptions, Form, Input, InputNumber, Row, Select, Space, Statistic, Switch, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import {
  evaluateAlerts,
  fetchAlertSummary,
  fetchNotificationConfig,
  testNotification,
  updateNotificationConfig,
  type AlertEvaluationResponse,
  type AlertIncidentItem,
  type AlertRuleItem,
  type NotificationConfig,
  type AlertSummaryResponse,
  type SloIndicatorItem,
} from "../api/client";

const statusColor: Record<string, string> = {
  passed: "green",
  info: "blue",
  warning: "gold",
  failed: "red",
  critical: "red",
  error: "red",
  open: "gold",
  closed: "green",
};

function safeError(error: unknown): string {
  return error instanceof Error ? error.message : "接口请求失败";
}

export function AlertsPage() {
  const [summary, setSummary] = useState<AlertSummaryResponse | null>(null);
  const [notification, setNotification] = useState<NotificationConfig | null>(null);
  const [evaluation, setEvaluation] = useState<AlertEvaluationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [notificationLoading, setNotificationLoading] = useState(false);
  const [notificationForm] = Form.useForm<{ enabled: boolean; webhook_url: string; secret: string; min_level: string; events: string; dry_run: boolean; cooldown_seconds: number }>();

  const load = async () => {
    setLoading(true);
    try {
      const [alertSummary, notificationConfig] = await Promise.all([
        fetchAlertSummary(),
        fetchNotificationConfig().catch(() => null),
      ]);
      setSummary(alertSummary);
      setNotification(notificationConfig);
      if (notificationConfig) {
        notificationForm.setFieldsValue({
          enabled: notificationConfig.enabled,
          webhook_url: notificationConfig.webhook_url || "",
          secret: "",
          min_level: notificationConfig.min_level,
          events: notificationConfig.events.join(","),
          dry_run: notificationConfig.dry_run,
          cooldown_seconds: notificationConfig.cooldown_seconds,
        });
      }
    } catch (err: unknown) {
      message.error(safeError(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const onEvaluate = async () => {
    try {
      const result = await evaluateAlerts();
      setEvaluation(result);
      message.success(`告警评估完成：${result.status}`);
      await load();
    } catch (err: unknown) {
      message.error(safeError(err));
    }
  };

  const onSaveNotification = async () => {
    setNotificationLoading(true);
    try {
      const values = await notificationForm.validateFields();
      const result = await updateNotificationConfig({
        enabled: values.enabled,
        webhook_url: values.webhook_url,
        secret: values.secret || undefined,
        min_level: values.min_level,
        events: values.events.split(",").map((item) => item.trim()).filter(Boolean),
        dry_run: values.dry_run,
        cooldown_seconds: values.cooldown_seconds,
      });
      setNotification(result);
      message.success(result.message);
      await load();
    } catch (err: unknown) {
      message.error(safeError(err));
    } finally {
      setNotificationLoading(false);
    }
  };

  const onTestNotification = async (send: boolean) => {
    setNotificationLoading(true);
    try {
      const result = await testNotification(send);
      message.success(result.message);
      await load();
    } catch (err: unknown) {
      message.error(safeError(err));
    } finally {
      setNotificationLoading(false);
    }
  };

  const rules = summary?.rules ?? [];
  const slo = summary?.slo.indicators ?? [];
  const incidents = evaluation?.incidents ?? summary?.incidents ?? [];
  const preview = evaluation?.notification_preview ?? summary?.notification_preview ?? "等待接口返回";

  const ruleColumns: ColumnsType<AlertRuleItem> = [
    { title: "规则", dataIndex: "title", key: "title" },
    { title: "级别", dataIndex: "severity", key: "severity", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
    { title: "SLO", dataIndex: "slo_target", key: "slo_target" },
    { title: "静默", dataIndex: "silence_minutes", key: "silence_minutes", render: (value: number) => `${value} 分钟` },
    { title: "Runbook", dataIndex: "runbook_hint", key: "runbook_hint" },
  ];

  const sloColumns: ColumnsType<SloIndicatorItem> = [
    { title: "指标", dataIndex: "title", key: "title" },
    { title: "目标", dataIndex: "target", key: "target" },
    { title: "当前", dataIndex: "current", key: "current" },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
    { title: "说明", dataIndex: "message", key: "message" },
  ];

  const incidentColumns: ColumnsType<AlertIncidentItem> = [
    { title: "事件", dataIndex: "title", key: "title" },
    { title: "级别", dataIndex: "severity", key: "severity", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
    { title: "对象", dataIndex: "subject", key: "subject" },
    { title: "原因", dataIndex: "reason", key: "reason" },
    { title: "处理建议", dataIndex: "recommended_action", key: "recommended_action" },
  ];

  return (
    <div className="page-stack">
      <Space align="center" wrap>
        <Typography.Title level={2} style={{ margin: 0 }}>告警中心</Typography.Title>
        <Button onClick={load} loading={loading}>刷新</Button>
        <Button type="primary" onClick={onEvaluate}>运行告警评估</Button>
      </Space>
      <Alert type={notification?.sends_network ? "success" : "info"} showIcon message={notification?.sends_network ? "飞书错误通知会按配置发送" : "飞书错误通知当前不会实际发送"} description={notification?.message ?? "可在本页配置飞书 webhook、dry-run、等级阈值和冷却时间；不会切换正式域名。"} />
      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}><Card><Statistic title="整体状态" value={evaluation?.status ?? summary?.status ?? "加载中"} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="告警规则" value={rules.length} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="当前事件" value={incidents.length} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="外部发送" value={summary?.external_send_enabled ? "开启" : "关闭"} /></Card></Col>
      </Row>
      <Card title="告警评估闭环">
        <Descriptions column={2} size="small">
          <Descriptions.Item label="trace_id">{evaluation?.trace_id ?? "尚未运行"}</Descriptions.Item>
          <Descriptions.Item label="audit_trace_id">{evaluation?.audit_trace_id ?? "尚未写入"}</Descriptions.Item>
          <Descriptions.Item label="dry_run">{evaluation?.dry_run ?? true ? "是" : "否"}</Descriptions.Item>
          <Descriptions.Item label="外部发送">{evaluation?.external_send_enabled ? "开启" : "关闭"}</Descriptions.Item>
          <Descriptions.Item label="结论" span={2}>{evaluation?.message ?? "等待运行告警评估"}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="飞书错误通知配置">
        <Form form={notificationForm} layout="vertical">
          <Row gutter={[16, 0]}>
            <Col xs={24} md={12}>
              <Form.Item label="飞书 webhook" name="webhook_url">
                <Input placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="签名 secret" name="secret">
                <Input.Password placeholder={notification?.secret_configured ? "已配置；留空保持不变" : "可选"} />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="启用通知" name="enabled" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="dry-run 演练" name="dry_run" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="最低级别" name="min_level">
                <Select options={[{ value: "warn", label: "warn" }, { value: "error", label: "error" }, { value: "critical", label: "critical" }]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item label="冷却秒数" name="cooldown_seconds">
                <InputNumber min={30} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item label="事件白名单" name="events">
                <Input.TextArea rows={2} placeholder="backend.error,worker.error,alert.evaluation.warning" />
              </Form.Item>
            </Col>
          </Row>
          <Space wrap>
            <Button type="primary" loading={notificationLoading} onClick={() => void onSaveNotification()}>保存通知配置</Button>
            <Button loading={notificationLoading} onClick={() => void onTestNotification(false)}>只检查配置</Button>
            <Button danger loading={notificationLoading} onClick={() => void onTestNotification(true)}>发送测试飞书</Button>
          </Space>
          <Descriptions size="small" column={2} style={{ marginTop: 16 }}>
            <Descriptions.Item label="配置来源">{notification?.source ?? "未加载"}</Descriptions.Item>
            <Descriptions.Item label="配置文件">{notification?.config_path ?? "未加载"}</Descriptions.Item>
            <Descriptions.Item label="webhook">{notification?.webhook_configured ? "已配置" : "未配置"}</Descriptions.Item>
            <Descriptions.Item label="外部发送">{notification?.sends_network ? "开启" : "关闭"}</Descriptions.Item>
          </Descriptions>
        </Form>
      </Card>
      <Card title="SLO 摘要">
        <Table columns={sloColumns} dataSource={slo} rowKey="key" pagination={false} />
      </Card>
      <Card title="当前告警事件">
        <Table columns={incidentColumns} dataSource={incidents} rowKey="key" pagination={false} />
      </Card>
      <Card title="内置告警规则">
        <Table columns={ruleColumns} dataSource={rules} rowKey="key" pagination={false} />
      </Card>
      <Card title={notification?.sends_network ? "飞书通知预览（按配置可发送）" : "飞书通知预览"}>
        <Typography.Paragraph copyable style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>{preview}</Typography.Paragraph>
      </Card>
    </div>
  );
}
