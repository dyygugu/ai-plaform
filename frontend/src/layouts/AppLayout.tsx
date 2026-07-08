import {
  AlertOutlined, BarChartOutlined, CloudServerOutlined, DashboardOutlined,
  FileDoneOutlined, MedicineBoxOutlined, RobotOutlined, SafetyCertificateOutlined, SettingOutlined, TeamOutlined, ToolOutlined, UnorderedListOutlined,
} from "@ant-design/icons";
import type { MenuProps } from "antd";
import { Button, Input, Layout, Menu, Modal, Space, Tag, Typography, message } from "antd";
import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { loginToPlatform } from "../api/client";

const { Header, Sider, Content } = Layout;

const menuItems: MenuProps["items"] = [
  { key: "/", icon: <DashboardOutlined />, label: <Link to="/">生产驾驶舱</Link> },
  { key: "/accounts", icon: <TeamOutlined />, label: <Link to="/accounts">账号与登录</Link> },
  { key: "/ability-workbench", icon: <RobotOutlined />, label: <Link to="/ability-workbench">AI标注能力工作台</Link> },
  { key: "/tasks", icon: <UnorderedListOutlined />, label: <Link to="/tasks">任务与待处理</Link> },
  { key: "/earnings", icon: <BarChartOutlined />, label: <Link to="/earnings">收益监控</Link> },
  { key: "/ai", icon: <RobotOutlined />, label: <Link to="/ai">AI聊天/API配置</Link> },
  {
    key: "ops-system",
    icon: <MedicineBoxOutlined />,
    label: "运维与系统",
    children: [
      { key: "/ops", icon: <FileDoneOutlined />, label: <Link to="/ops">故障定位台</Link> },
      { key: "/alerts", icon: <AlertOutlined />, label: <Link to="/alerts">告警通知配置</Link> },
      { key: "/workers", icon: <ToolOutlined />, label: <Link to="/workers">多 Worker</Link> },
      { key: "/backups", icon: <CloudServerOutlined />, label: <Link to="/backups">备份恢复</Link> },
      { key: "/security", icon: <SafetyCertificateOutlined />, label: <Link to="/security">权限与审计</Link> },
      { key: "/settings", icon: <SettingOutlined />, label: <Link to="/settings">系统设置</Link> },
    ],
  },
];

export function AppLayout() {
  const location = useLocation();
  const [loginModalOpen, setLoginModalOpen] = useState(false);
  const [phoneInput, setPhoneInput] = useState("");
  const [passwordInput, setPasswordInput] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [tokenConfigured, setTokenConfigured] = useState(() => typeof window !== "undefined" && Boolean(window.localStorage.getItem("aidpApiToken") || window.sessionStorage.getItem("aidpApiToken")));
  const [authError, setAuthError] = useState("");

  useEffect(() => {
    const onAuthError = (event: Event) => {
      const detail = (event as CustomEvent<{ status?: number; message?: string }>).detail;
      setAuthError(detail?.message || "当前功能需要先登录平台。");
      setLoginModalOpen(true);
    };
    window.addEventListener("aidp-api-auth-error", onAuthError);
    return () => window.removeEventListener("aidp-api-auth-error", onAuthError);
  }, []);

  const submitLogin = async () => {
    const phone = phoneInput.trim();
    const password = passwordInput;
    if (!phone || !password) {
      message.warning("请输入手机号和密码。");
      return;
    }
    setLoginLoading(true);
    try {
      const result = await loginToPlatform({ phone, password });
      window.localStorage.setItem("aidpApiToken", result.access_token);
      window.sessionStorage.removeItem("aidpApiToken");
      setTokenConfigured(true);
      setLoginModalOpen(false);
      setAuthError("");
      setPasswordInput("");
      message.success(`平台登录成功：${result.phone_masked}`);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "平台登录失败。");
    } finally {
      setLoginLoading(false);
    }
  };

  const clearLogin = () => {
    window.localStorage.removeItem("aidpApiToken");
    window.sessionStorage.removeItem("aidpApiToken");
    setPasswordInput("");
    setTokenConfigured(false);
    message.success("本机平台登录已清除。");
  };

  return (
    <Layout className="app-shell">
      <Sider breakpoint="lg" collapsedWidth={0} className="app-sider">
        <div className="brand"><CloudServerOutlined /><span>AIDP Monitor</span></div>
        <Menu theme="dark" mode="inline" selectedKeys={[location.pathname]} items={menuItems} />
      </Sider>
      <Layout className="app-main">
        <Header className="app-header">
          <Typography.Title level={4} className="app-title">AIDP 做题生产平台</Typography.Title>
          <Space style={{ marginLeft: "auto" }}>
            <Tag color={tokenConfigured ? "green" : "gold"}>{tokenConfigured ? "平台已登录" : "需要平台登录"}</Tag>
            <Button onClick={() => setLoginModalOpen(true)}>平台登录</Button>
          </Space>
        </Header>
        <Content className="app-content"><Outlet /></Content>
      </Layout>
      <Modal
        title="平台登录"
        open={loginModalOpen}
        onOk={() => void submitLogin()}
        confirmLoading={loginLoading}
        okText="登录"
        cancelText="取消"
        onCancel={() => setLoginModalOpen(false)}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary">公网平台需要先登录；登录成功后，本机浏览器会自动保存会话并访问各功能。</Typography.Text>
          {authError ? <Typography.Text type="danger">{authError}</Typography.Text> : null}
          <Input value={phoneInput} placeholder="手机号" autoComplete="username" onChange={(event) => setPhoneInput(event.target.value)} />
          <Input.Password value={passwordInput} placeholder="密码" autoComplete="current-password" onPressEnter={() => void submitLogin()} onChange={(event) => setPasswordInput(event.target.value)} />
          <Button danger onClick={clearLogin} disabled={!tokenConfigured}>退出本机平台登录</Button>
        </Space>
      </Modal>
    </Layout>
  );
}
