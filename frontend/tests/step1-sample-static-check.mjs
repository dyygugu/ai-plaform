import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const pagePath = resolve(root, "src/pages/AbilityWorkbenchPage.tsx");
const page = readFileSync(pagePath, "utf8");

assert.match(page, /function preferredSampleAccountId/, "Step1 需要有统一的样本来源账号选择 helper");
assert.match(page, /const accountIds = new Set\(accounts\.map\(\(account\) => account\.user_id\)\)/, "样本来源账号必须以当前账号列表为准，不能信任陈旧任务目录");
assert.match(page, /taskAccountIds\.find\(\(accountId\) => accountIds\.has\(accountId\)\)/, "任务覆盖账号必须先确认仍存在于账号列表");
assert.match(page, /return matchedTaskAccountId \|\| accounts\[0\]\?\.user_id \|\| "";/, "没有有效任务账号时只能回退到当前账号列表第一个账号，否则为空禁用同步");
assert.match(page, /setSyncConfig\(\(current\)\s*=>\s*\(\{[\s\S]*account_id:\s*preferredSampleAccountId\(/, "切换任务时必须自动选择可用样本来源账号，不能默认发送空账号");
assert.match(page, /disabled=\{!syncConfig\.account_id\}/, "没有样本来源账号时，不能允许触发同步样本接口");
assert.match(page, /response\?\.data\?\.detail/, "Step1 接口错误必须优先展示后端 detail，而不是只显示 code500");
assert.doesNotMatch(page, /样本来源账号（默认任务源）/, "NAS 未配置任务源账号时，文案不能继续暗示空账号可用");
