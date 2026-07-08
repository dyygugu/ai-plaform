import { Alert, Button, Card, Descriptions, Form, Input, Space, Tag, Typography, message } from "antd";
import { useEffect, useState } from "react";

import { fetchRuntimeSettings, fetchTaskRules, updateTaskRules, updateTaskSourceAccount, type RuntimeSettingsResponse, type TaskRuleConfigResponse } from "../api/client";

export function SettingsPage() {
  const [settings, setSettings] = useState<RuntimeSettingsResponse | null>(null);
  const [rules, setRules] = useState<TaskRuleConfigResponse | null>(null);
  const [prefixRulesText, setPrefixRulesText] = useState("");
  const [manualShortNamesText, setManualShortNamesText] = useState("{}");
  const [form] = Form.useForm<{ taskSourceAccountUserId: string }>();

  const loadSettings = async () => {
    const response = await fetchRuntimeSettings();
    setSettings(response);
    form.setFieldValue("taskSourceAccountUserId", response.task_source_account_user_id);
  };

  const loadRules = async () => {
    const response = await fetchTaskRules();
    setRules(response);
    setPrefixRulesText(response.prefix_rules.join("\n"));
    setManualShortNamesText(JSON.stringify(response.manual_short_names, null, 2));
  };

  useEffect(() => {
    void Promise.all([loadSettings(), loadRules()]);
  }, []);

  const handleSubmit = async (values: { taskSourceAccountUserId: string }) => {
    const result = await updateTaskSourceAccount(values.taskSourceAccountUserId);
    message.success(result.message);
    await loadSettings();
  };

  const handleRuleSubmit = async () => {
    let manualShortNames: Record<string, string> = {};
    try {
      manualShortNames = manualShortNamesText.trim() ? JSON.parse(manualShortNamesText) : {};
    } catch {
      message.error("单任务手动简称必须是 JSON 对象");
      return;
    }
    const prefixRules = prefixRulesText.split("\n").map((item) => item.trim()).filter(Boolean);
    const response = await updateTaskRules({ prefix_rules: prefixRules, manual_short_names: manualShortNames });
    setRules(response);
    message.success("任务目录治理配置已保存，刷新任务目录后生效。");
  };

  return (
    <div className="page-stack">
      <Typography.Title level={2}>系统设置</Typography.Title>
      <Card title="运行配置">
        <Descriptions bordered column={1}>
          <Descriptions.Item label="任务页来源账号">{settings?.task_source_account_user_id || "未配置"}</Descriptions.Item>
          <Descriptions.Item label="当前基础地址">{settings?.public_base_url ?? "http://localhost:8789"}</Descriptions.Item>
          <Descriptions.Item label="正式域名">{settings?.production_domain ?? "manage.51gugu.uk"}</Descriptions.Item>
          <Descriptions.Item label="域名切换">
            {settings?.production_domain_switch_deferred ?? true ? <Tag color="gold">最后处理</Tag> : <Tag color="green">可切换</Tag>}
          </Descriptions.Item>
        </Descriptions>
      </Card>
      <Card title="任务目录治理（运维）">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert type="info" showIcon message="日常任务调控请去任务与待处理；这里仅维护来源账号和任务简称规则。" />
          <Typography.Text type="secondary">修改后会写入审计日志，并提示回到任务看板执行手动刷新；不会切换正式域名。</Typography.Text>
          <Form form={form} layout="inline" onFinish={(values) => void handleSubmit(values)}>
            <Form.Item label="来源账号" name="taskSourceAccountUserId" rules={[{ required: true, message: "请输入来源账号" }]}>
              <Input style={{ width: 280 }} />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit">保存</Button>
            </Form.Item>
          </Form>
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text>自动去除前缀（每行一个）</Typography.Text>
            <Input.TextArea rows={3} placeholder="RFT人标_" value={prefixRulesText} onChange={(event) => setPrefixRulesText(event.target.value)} />
            <Typography.Text>单任务手动简称 JSON（任务ID 到 简称）</Typography.Text>
            <Input.TextArea rows={4} placeholder='{"7634***9806":"美观度"}' value={manualShortNamesText} onChange={(event) => setManualShortNamesText(event.target.value)} />
            <Space>
              <Button onClick={() => void handleRuleSubmit()}>保存目录治理配置</Button>
              <Typography.Text type="secondary">当前前缀 {rules?.prefix_rules.length ?? 0} 条，手动简称 {Object.keys(rules?.manual_short_names ?? {}).length} 条</Typography.Text>
            </Space>
          </Space>
        </Space>
      </Card>
    </div>
  );
}
