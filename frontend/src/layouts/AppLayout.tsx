import {
  AlertOutlined, BarChartOutlined, CloudServerOutlined, DashboardOutlined,
  FileDoneOutlined, MedicineBoxOutlined, RobotOutlined, SafetyCertificateOutlined, SettingOutlined, TeamOutlined, ToolOutlined, UnorderedListOutlined,
} from "@ant-design/icons";
import type { MenuProps } from "antd";
import { Layout, Menu, Typography } from "antd";
import { Link, Outlet, useLocation } from "react-router-dom";

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
      { key: "/workers", icon: <ToolOutlined />, label: <Link to="/workers">执行设备管理</Link> },
      { key: "/backups", icon: <CloudServerOutlined />, label: <Link to="/backups">备份恢复</Link> },
      { key: "/security", icon: <SafetyCertificateOutlined />, label: <Link to="/security">权限与审计</Link> },
      { key: "/settings", icon: <SettingOutlined />, label: <Link to="/settings">系统设置</Link> },
    ],
  },
];

export function AppLayout() {
  const location = useLocation();
  return <Layout className="app-shell"><Sider breakpoint="lg" collapsedWidth={0} className="app-sider"><div className="brand"><CloudServerOutlined /><span>AIDP Monitor</span></div><Menu theme="dark" mode="inline" selectedKeys={[location.pathname]} items={menuItems} /></Sider><Layout className="app-main"><Header className="app-header"><Typography.Title level={4} className="app-title">AIDP 做题生产平台</Typography.Title></Header><Content className="app-content"><Outlet /></Content></Layout></Layout>;
}
