import { Button, Card, Descriptions, Form, Input, Space, Tag, Typography, message } from "antd";
import { useEffect, useState } from "react";

import { fetchRuntimeSettings, updateTaskSourceAccount, type RuntimeSettingsResponse } from "../api/client";

export function SettingsPage() {
  const [settings, setSettings] = useState<RuntimeSettingsResponse | null>(null);
  const [form] = Form.useForm<{ taskSourceAccountUserId: string }>();

  const loadSettings = async () => {
    const response = await fetchRuntimeSettings();
    setSettings(response);
    form.setFieldValue("taskSourceAccountUserId", response.task_source_account_user_id);
  };

  useEffect(() => {
    void loadSettings();
  }, []);

  const handleSubmit = async (values: { taskSourceAccountUserId: string }) => {
    const result = await updateTaskSourceAccount(values.taskSourceAccountUserId);
    message.success(result.message);
    await loadSettings();
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
      <Card title="任务页来源账号">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary">修改后会写入审计日志，并提示回到任务看板执行手动刷新；不会切换正式域名。</Typography.Text>
          <Form form={form} layout="inline" onFinish={(values) => void handleSubmit(values)}>
            <Form.Item label="来源账号" name="taskSourceAccountUserId" rules={[{ required: true, message: "请输入来源账号" }]}>
              <Input style={{ width: 280 }} />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit">保存</Button>
            </Form.Item>
          </Form>
        </Space>
      </Card>
    </div>
  );
}
