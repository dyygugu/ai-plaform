import assert from "node:assert/strict";
import fs from "node:fs";

const alertsPage = fs.readFileSync(new URL("../src/pages/AlertsPage.tsx", import.meta.url), "utf8");

assert.match(alertsPage, /提醒（不一定要立刻处理）/, "最低通知级别需要用人能看懂的提醒文案");
assert.match(alertsPage, /一般（可以稍后处理）/, "最低通知级别需要用人能看懂的一般文案");
assert.match(alertsPage, /紧急（必须立刻处理）/, "最低通知级别需要用人能看懂的紧急文案");
assert.match(alertsPage, /飞书通知按“提醒 \/ 一般 \/ 紧急”分级/, "告警页需要说明飞书通知的人话分级");
assert.match(alertsPage, /failed: "紧急"/, "失败状态不能被显示成一般等级");
assert.match(alertsPage, /webhook_url: ""/, "已配置 webhook 不能把脱敏值回填到可保存表单");
assert.match(alertsPage, /webhook_url: values\.webhook_url \|\| undefined/, "空 webhook 输入应省略，避免覆盖已保存 webhook");
assert.match(alertsPage, /已配置；留空保持不变/, "webhook 已配置时需要提示留空保持不变");

console.log("notification_ux_static_check_ok=true");
