import { Button, Card, Col, Form, Input, InputNumber, Row, Space, Statistic, Table, Tag, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";

import {
  exportEarnings,
  fetchEarnings,
  updateEarningsLedgerRunPrice,
  updateEarningsPriceConfig,
  type EarningsItem,
  type EarningsLedgerAccountItem,
  type EarningsLedgerRunItem,
  type EarningsLedgerTaskItem,
  type EarningsSummary,
} from "../api/client";

const snapshotColumns: ColumnsType<EarningsItem> = [
  { title: "账号", dataIndex: "account_user_id", key: "account_user_id" },
  { title: "收入项1", key: "income1", render: (_, row) => `${row.income_1_name}: ${row.income_1_value}` },
  { title: "收入项2", key: "income2", render: (_, row) => `${row.income_2_name}: ${row.income_2_value}` },
  { title: "收入项3", key: "income3", render: (_, row) => `${row.income_3_name}: ${row.income_3_value}` },
  { title: "今日收益", dataIndex: "today_income", key: "today_income" },
  { title: "小时收益", dataIndex: "hourly_income", key: "hourly_income" },
];

function safeError(error: unknown): string {
  return error instanceof Error ? error.message : "接口请求失败";
}

function timeText(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "-";
}

function money(value: number | null | undefined): string {
  return Number(value ?? 0).toFixed(2);
}

export function EarningsPage() {
  const [data, setData] = useState<EarningsSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [priceSaving, setPriceSaving] = useState("");
  const [priceEdits, setPriceEdits] = useState<Record<string, number>>({});
  const [form] = Form.useForm<{ unit_price: number; currency: string; billable_unit: string }>();

  const load = async () => {
    setLoading(true);
    try {
      const result = await fetchEarnings();
      setData(result);
      form.setFieldsValue(result.price_config);
      setPriceEdits(Object.fromEntries(result.ledger_items.flatMap((task) => task.runs.map((run) => [run.run_id, run.unit_price]))));
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const onSavePrice = async () => {
    setLoading(true);
    try {
      const values = await form.validateFields();
      await updateEarningsPriceConfig(values);
      message.success("默认单题价格已保存，新生成的记账记录会使用这个价格");
      await load();
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setLoading(false);
    }
  };

  const onSaveRunPrice = async (run: EarningsLedgerRunItem) => {
    setPriceSaving(run.run_id);
    try {
      await updateEarningsLedgerRunPrice(run.run_id, { unit_price: priceEdits[run.run_id] ?? run.unit_price });
      message.success("保存单价成功");
      await load();
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setPriceSaving("");
    }
  };

  const onExport = async () => {
    setLoading(true);
    try {
      const result = await exportEarnings();
      setData(result);
      message.success(`Excel 已导出：${result.export_path}`);
    } catch (error: unknown) {
      message.error(safeError(error));
    } finally {
      setLoading(false);
    }
  };

  const accountColumns: ColumnsType<EarningsLedgerAccountItem> = [
    { title: "账号", dataIndex: "display_name", key: "display_name", render: (value: string, row) => value || row.account_user_id },
    { title: "完成题目数", dataIndex: "completed_count", key: "completed_count", align: "right" },
    { title: "金额", dataIndex: "amount", key: "amount", align: "right", render: money },
    { title: "开始时间", dataIndex: "started_at", key: "started_at", render: timeText },
    { title: "截止时间", dataIndex: "finished_at", key: "finished_at", render: timeText },
  ];

  const runColumns: ColumnsType<EarningsLedgerRunItem> = [
    { title: "完成题目数", dataIndex: "completed_count", key: "completed_count", align: "right" },
    { title: "金额", dataIndex: "amount", key: "amount", align: "right", render: money },
    { title: "开始时间", dataIndex: "started_at", key: "started_at", render: timeText },
    { title: "截止时间", dataIndex: "finished_at", key: "finished_at", render: timeText },
    {
      title: "单题价格",
      key: "unit_price",
      render: (_, run) => (
        <Space>
          <InputNumber min={0} precision={2} value={priceEdits[run.run_id] ?? run.unit_price} onChange={(value) => setPriceEdits((current) => ({ ...current, [run.run_id]: Number(value ?? 0) }))} />
          <Button size="small" loading={priceSaving === run.run_id} onClick={() => void onSaveRunPrice(run)}>保存单价</Button>
        </Space>
      ),
    },
  ];

  const ledgerColumns: ColumnsType<EarningsLedgerTaskItem> = [
    { title: "任务及任务ID", key: "task", render: (_, row) => (
      <Space direction="vertical" size={0}>
        <Typography.Text strong>{row.task_name || row.task_id}</Typography.Text>
        <Typography.Text type="secondary">{row.task_id}</Typography.Text>
      </Space>
    ) },
    { title: "总完成题目数", dataIndex: "completed_count", key: "completed_count", align: "right" },
    { title: "总金额", dataIndex: "amount", key: "amount", align: "right", render: money },
    { title: "开始时间", dataIndex: "started_at", key: "started_at", render: timeText },
    { title: "截止时间", dataIndex: "finished_at", key: "finished_at", render: timeText },
  ];

  return (
    <div className="page-stack">
      <Typography.Title level={2}>收益监控</Typography.Title>
      <Typography.Text type="secondary">自动记账根据统一生产刷新里的进行中数量变化生成；连续 4 次刷新不变后截止，完成题数=截止数-变化前基线。</Typography.Text>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}><Card><Statistic title="个人中心今日收益" value={data?.today_income_total ?? 0} precision={2} /></Card></Col>
        <Col xs={24} md={8}><Card><Statistic title="个人中心小时收益" value={data?.hourly_income_total ?? 0} precision={2} /></Card></Col>
        <Col xs={24} md={8}><Card><Statistic title="自动记账总金额" value={data?.ledger_total_amount ?? 0} precision={2} suffix={data?.price_config.currency ?? "CNY"} /></Card></Col>
      </Row>

      <Card title="默认价格配置" extra={<Typography.Text type="secondary">新生成的每次任务记录会先使用默认单价，之后可在下方单独修改。</Typography.Text>}>
        <Form form={form} layout="inline" initialValues={{ unit_price: 0, currency: "CNY", billable_unit: "完成题" }}>
          <Form.Item label="单题价格" name="unit_price" rules={[{ required: true, message: "请填写单题价格" }]}>
            <InputNumber min={0} precision={2} />
          </Form.Item>
          <Form.Item label="币种" name="currency">
            <Input style={{ width: 100 }} />
          </Form.Item>
          <Form.Item label="计费单位" name="billable_unit">
            <Input style={{ width: 120 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" loading={loading} onClick={() => void onSavePrice()}>保存默认价格</Button>
          </Form.Item>
        </Form>
      </Card>

      <Card title="自动记账" extra={<Tag color="blue">三级：任务 / 每次启动 / 账号</Tag>}>
        <Table
          columns={ledgerColumns}
          dataSource={data?.ledger_items ?? []}
          rowKey="task_id"
          loading={loading}
          pagination={{ pageSize: 10 }}
          expandable={{
            expandedRowRender: (task) => (
              <Table<EarningsLedgerRunItem>
                columns={runColumns}
                dataSource={task.runs}
                rowKey="run_id"
                pagination={false}
                expandable={{
                  expandedRowRender: (run) => <Table columns={accountColumns} dataSource={run.accounts} rowKey="account_run_id" pagination={false} />,
                }}
              />
            ),
          }}
        />
      </Card>

      <Card title="个人中心原始收益三项" extra={<Button type="primary" loading={loading} onClick={() => void onExport()}>导出 Excel</Button>}>
        <Table columns={snapshotColumns} dataSource={data?.items ?? []} rowKey="id" loading={loading} />
      </Card>
    </div>
  );
}
