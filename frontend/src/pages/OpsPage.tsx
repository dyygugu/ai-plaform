import { Alert, Button, Card, Col, Descriptions, List, Row, Space, Statistic, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Link } from "react-router-dom";
import { useEffect, useState } from "react";

import {
  fetchAlertSummary,
  fetchDataQualitySummary,
  fetchFaultDiagnosis,
  fetchObservabilitySummary,
  fetchOperationalRiskSummary,
  fetchOpsJobs,
  fetchProductionDashboard,
  fetchReleaseGate,
  runOpsJob,
  runRestoreDrill,
  type AlertIncidentItem,
  type DataQualitySummaryResponse,
  type FaultDiagnosisItem,
  type FaultDiagnosisResponse,
  type MaintenanceJobDefinitionItem,
  type MaintenanceJobRunItem,
  type ObservabilitySummary,
  type OperationalRiskItem,
  type OperationalRiskSummaryResponse,
  type ProductionDashboardSummary,
  type ReleaseGateCheckItem,
  type ReleaseGateResponse,
  type RestoreDrillItem,
  type TimelineEventItem,
} from "../api/client";

const statusColor: Record<string, string> = {
  completed: "green",
  passed: "green",
  info: "blue",
  warning: "gold",
  blocked: "gold",
  running: "blue",
  failed: "red",
  critical: "red",
  error: "red",
  open: "gold",
};

function safeError(error: unknown): string {
  return error instanceof Error ? error.message : "接口请求失败";
}

export function OpsPage() {
  const [dashboard, setDashboard] = useState<ProductionDashboardSummary | null>(null);
  const [risk, setRisk] = useState<OperationalRiskSummaryResponse | null>(null);
  const [diagnosis, setDiagnosis] = useState<FaultDiagnosisResponse | null>(null);
  const [alertIncidents, setAlertIncidents] = useState<AlertIncidentItem[]>([]);
  const [observability, setObservability] = useState<ObservabilitySummary | null>(null);
  const [dataQuality, setDataQuality] = useState<DataQualitySummaryResponse | null>(null);
  const [jobs, setJobs] = useState<MaintenanceJobDefinitionItem[]>([]);
  const [runs, setRuns] = useState<MaintenanceJobRunItem[]>([]);
  const [gate, setGate] = useState<ReleaseGateResponse | null>(null);
  const [drill, setDrill] = useState<RestoreDrillItem | null>(null);
  const [loading, setLoading] = useState(false);

  const loadOps = async () => {
    setLoading(true);
    try {
      const [production, riskSummary, faultDiagnosis, alertSummary, obsSummary, qualitySummary, jobSummary, releaseGate] = await Promise.all([
        fetchProductionDashboard(),
        fetchOperationalRiskSummary(),
        fetchFaultDiagnosis(),
        fetchAlertSummary(),
        fetchObservabilitySummary(),
        fetchDataQualitySummary(),
        fetchOpsJobs(),
        fetchReleaseGate(),
      ]);
      setDashboard(production);
      setRisk(riskSummary);
      setDiagnosis(faultDiagnosis);
      setAlertIncidents(alertSummary.incidents);
      setObservability(obsSummary);
      setDataQuality(qualitySummary);
      setJobs(jobSummary.jobs);
      setRuns(jobSummary.recent_runs);
      setGate(releaseGate);
    } catch (err: unknown) {
      message.error(safeError(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadOps(); }, []);

  const onDrill = async () => {
    try {
      const result = await runRestoreDrill();
      setDrill(result);
      message.success("恢复演练通过");
      await loadOps();
    } catch (err: unknown) {
      message.error(safeError(err));
    }
  };

  const onRunJob = async (jobKey: string, dryRun: boolean) => {
    try {
      const result = await runOpsJob(jobKey, dryRun);
      message.success(`${dryRun ? "预演" : "运行"}完成：${result.message}`);
      await loadOps();
    } catch (err: unknown) {
      message.error(safeError(err));
    }
  };

  const firstFault = diagnosis?.primary ?? null;

  const jobColumns: ColumnsType<MaintenanceJobDefinitionItem> = [
    { title: "任务", dataIndex: "title", key: "title" },
    { title: "用途", dataIndex: "description", key: "description" },
    { title: "最近状态", key: "last_run", render: (_, record) => record.last_run ? <Tag color={statusColor[record.last_run.status] ?? "default"}>{record.last_run.status}</Tag> : <Tag>未运行</Tag> },
    { title: "操作", key: "actions", render: (_, record) => <Space wrap><Button size="small" onClick={() => onRunJob(record.key, true)}>预演</Button><Button size="small" type="primary" onClick={() => onRunJob(record.key, false)}>运行</Button></Space> },
  ];

  const runColumns: ColumnsType<MaintenanceJobRunItem> = [
    { title: "任务", dataIndex: "job_key", key: "job_key" },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
    { title: "消息", dataIndex: "message", key: "message" },
    { title: "trace_id", dataIndex: "trace_id", key: "trace_id" },
  ];

  const gateColumns: ColumnsType<ReleaseGateCheckItem> = [
    { title: "门禁", dataIndex: "title", key: "title" },
    { title: "状态", dataIndex: "status", key: "status", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
    { title: "说明", dataIndex: "message", key: "message" },
  ];

  const diagnosisColumns: ColumnsType<FaultDiagnosisItem> = [
    { title: "级别", dataIndex: "severity", key: "severity", render: (value: string) => <Tag color={statusColor[value] ?? "default"}>{value}</Tag> },
    { title: "错误位置", dataIndex: "error_location", key: "error_location" },
    { title: "准确错误信息", dataIndex: "accurate_error", key: "accurate_error" },
    { title: "影响范围", dataIndex: "affected_scope", key: "affected_scope" },
    { title: "首个发现来源", dataIndex: "first_seen_source", key: "first_seen_source" },
    { title: "下一步动作", dataIndex: "next_actions", key: "next_actions", render: (values: string[]) => <Space direction="vertical" size={0}>{values.map((value) => <Typography.Text key={value}>{value}</Typography.Text>)}</Space> },
    { title: "证据", dataIndex: "evidence_links", key: "evidence_links", render: (values: string[]) => <Space wrap>{values.map((value) => <Button key={value} size="small"><Link to={value}>打开</Link></Button>)}</Space> },
    {
      title: "Worker日志回放",
      dataIndex: "worker_log_replay",
      key: "worker_log_replay",
      render: (values: FaultDiagnosisItem["worker_log_replay"]) => values.length ? (
        <Space direction="vertical" size={0}>
          {values.slice(0, 2).map((item) => (
            <Typography.Text key={item.trace_id} type="secondary">
              {item.severity} · {item.worker_id} · 阶段步骤={item.stage || "-"}/{item.step || "-"} · 错误代码={item.error_code || "-"} · {item.error_detail || item.message} · trace_id={item.trace_id}
            </Typography.Text>
          ))}
        </Space>
      ) : "-",
    },
    { title: "升级提示", dataIndex: "escalation_hint", key: "escalation_hint" },
  ];

  const timelineItems = (observability?.recent_timeline ?? []).slice(0, 8);

  return (
    <div className="page-stack">
      <Space align="center" wrap>
        <Typography.Title level={2} style={{ margin: 0 }}>故障定位台</Typography.Title>
        <Button onClick={loadOps} loading={loading}>刷新</Button>
      </Space>
      <Alert
        type={firstFault ? (["critical", "failed", "error"].includes(firstFault.severity) ? "error" : "warning") : "success"}
        showIcon
        message={firstFault ? `第一时间看这里：${firstFault.error_location}` : "第一时间看这里：当前未发现阻塞故障"}
        description={firstFault ? `准确错误信息：${firstFault.accurate_error}；影响范围：${firstFault.affected_scope}；升级提示：${firstFault.escalation_hint}` : "运维页已合并风险清单、告警、观测、数据质量、异常记录、生产护栏和运行历史；备份与执行设备管理作为必要能力保留。"}
        action={firstFault?.evidence_links?.[0] ? <Button size="small"><Link to={firstFault.evidence_links[0]}>打开证据</Link></Button> : undefined}
      />

      <Row gutter={[16, 16]}>
        <Col xs={12} lg={4}><Card><Statistic title="风险" value={risk?.risk_count ?? 0} /></Card></Col>
        <Col xs={12} lg={4}><Card><Statistic title="Critical" value={risk?.critical_count ?? 0} /></Card></Col>
        <Col xs={12} lg={4}><Card><Statistic title="可定位故障" value={diagnosis?.fault_count ?? 0} /></Card></Col>
        <Col xs={12} lg={4}><Card><Statistic title="告警事件" value={alertIncidents.length} /></Card></Col>
        <Col xs={12} lg={4}><Card><Statistic title="真实账号" value={dashboard?.account_count ?? 0} /></Card></Col>
        <Col xs={12} lg={4}><Card><Statistic title="待处理" value={dashboard?.pending_total ?? 0} /></Card></Col>
      </Row>

      <Card title="当前错误定位" extra={<Typography.Text type="secondary">{diagnosis?.message ?? "等待诊断接口返回"}</Typography.Text>}>
        <Table columns={diagnosisColumns} dataSource={diagnosis?.items ?? []} rowKey="key" pagination={false} locale={{ emptyText: "当前无可定位故障；如仍异常，先看下方最近错误时间线。" }} />
      </Card>

      <Card title="最近错误时间线">
        <List
          dataSource={timelineItems}
          renderItem={(item: TimelineEventItem) => (
            <List.Item>
              <Space direction="vertical" size={2}>
                <Space wrap><Tag>{item.source}</Tag><Tag color={statusColor[item.severity] ?? "default"}>{item.severity}</Tag><Typography.Text strong>{item.title}</Typography.Text></Space>
                <Typography.Text>{item.message || "-"}</Typography.Text>
                <Typography.Text type="secondary">trace_id={item.trace_id} · {item.created_at}</Typography.Text>
              </Space>
            </List.Item>
          )}
        />
      </Card>

      <Card title="发布门禁与系统任务">
        <Descriptions column={2} size="small">
          <Descriptions.Item label="发布门禁">{gate?.ready_for_manual_domain_switch ? "可人工切换" : "未就绪"}</Descriptions.Item>
          <Descriptions.Item label="结论">{gate?.message ?? "等待门禁接口返回"}</Descriptions.Item>
        </Descriptions>
        <Table columns={gateColumns} dataSource={gate?.checks ?? []} rowKey="key" pagination={false} />
      </Card>

      <Card title="必要运维动作" extra={<Typography.Text type="secondary">只保留定位、恢复、门禁和证据生成相关动作。</Typography.Text>}>
        <Table columns={jobColumns} dataSource={jobs} rowKey="key" loading={loading} pagination={false} />
      </Card>

      <Card title="运行历史">
        <Table columns={runColumns} dataSource={runs} rowKey="id" pagination={{ pageSize: 5 }} />
      </Card>

      <Card title="恢复演练" extra={<Button type="primary" onClick={onDrill}>运行演练</Button>}>
        <Descriptions bordered column={1} size="small">
          <Descriptions.Item label="状态">{drill?.status ?? "未运行"}</Descriptions.Item>
          <Descriptions.Item label="说明">{drill?.message ?? "等待执行"}</Descriptions.Item>
          <Descriptions.Item label="trace_id">{drill?.trace_id ?? "-"}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="整合后的定位证据" extra={<Typography.Text type="secondary">这些详情页不再占侧边栏，故障时从本页聚合结果进入。</Typography.Text>}>
        <Space wrap>
          <Button><Link to="/observability">日志观测详情</Link></Button>
          <Button><Link to="/data-quality">数据校验详情</Link></Button>
          <Button><Link to="/incidents">异常处置记录</Link></Button>
          <Button><Link to="/production">生产护栏详情</Link></Button>
          <Button><Link to="/account-coverage">账号覆盖记录</Link></Button>
          <Button><Link to="/security">审计日志</Link></Button>
        </Space>
      </Card>

      <Card title="保留运维能力" extra={<Typography.Text type="secondary">备份恢复和执行设备管理是生产恢复/扩展能力，保留独立入口。</Typography.Text>}>
        <Space wrap>
          <Button type="primary"><Link to="/backups">备份恢复</Link></Button>
          <Button type="primary"><Link to="/workers">执行设备管理</Link></Button>
          <Button><Link to="/alerts">告警配置和飞书 webhook</Link></Button>
          <Button><Link to="/settings">系统设置</Link></Button>
        </Space>
      </Card>
    </div>
  );
}
