import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const layout = readFileSync("frontend/src/layouts/AppLayout.tsx", "utf8");
const client = readFileSync("frontend/src/api/client.ts", "utf8");
const indexHtml = readFileSync("frontend/index.html", "utf8");
const runtimeConfig = readFileSync("frontend/public/aidp-runtime-config.js", "utf8");
const tasksPage = readFileSync("frontend/src/pages/TasksPage.tsx", "utf8");
const abilityWorkbench = readFileSync("frontend/src/pages/AbilityWorkbenchPage.tsx", "utf8");

assert.match(layout, /平台登录/, "公网部署必须提供普通用户可理解的平台登录入口");
assert.match(layout, /手机号/, "平台登录必须使用手机号字段");
assert.match(layout, /密码/, "平台登录必须使用密码字段");
assert.match(layout, /aidp-api-auth-error/, "鉴权失败时必须自动提示配置 Token");
assert.match(layout, /localStorage\.setItem\("aidpApiToken"/, "Token 保存必须写入现有 aidpApiToken 键");
assert.match(layout, /localStorage\.removeItem\("aidpApiToken"/, "必须提供清除本机 Token 的入口");
assert.match(client, /loginToPlatform/, "前端 API 客户端必须提供平台登录方法");
assert.match(client, /\/auth\/login/, "平台登录必须调用后端登录接口");
assert.match(client, /VITE_AIDP_API_PREFIX/, "前端 API 前缀必须支持部署环境配置");
assert.match(client, /__AIDP_API_PREFIX__/, "前端 API 前缀必须支持后端运行时配置");
assert.ok(client.includes('replace(/\\/+/g, "/")'), "前端 API 前缀必须压缩重复斜杠");
assert.doesNotMatch(client, /baseURL: "\/api\/v1"/, "前端 API baseURL 不能硬编码 /api/v1");
assert.match(indexHtml, /aidp-runtime-config\.js/, "页面启动前必须加载后端运行时 API 前缀配置");
assert.doesNotMatch(runtimeConfig, /__AIDP_API_PREFIX__\s*=\s*["']\/api\/v1["']/, "静态 runtime fallback 不能覆盖 Vite/后端 API 前缀");
assert.match(client, /Authorization = `Bearer \$\{token\}`/, "网页登录会话必须使用 Authorization Bearer");
assert.match(client, /X-AIDP-API-Token/, "兼容 API token 必须继续携带 X-AIDP-API-Token");
assert.match(client, /token\.startsWith\("web\."\)/, "必须区分 web session 与 legacy API token");
assert.match(client, /status === 401 \|\| status === 403 \|\| status === 503/, "401/403/503 必须触发鉴权提示");
assert.match(client, /responseType: "blob"/, "下载本机助手安装包必须走 axios blob，才能携带 API Token header");
assert.doesNotMatch(client, /window\.open\("\/api\/v1\/local-agent\/releases\/latest\/download-/, "受保护下载不能用 window.open 直连接口");
assert.match(tasksPage, /openAccountTarget/, "任务页打开账号窗口必须调用受保护接口以携带登录 Token");
assert.doesNotMatch(tasksPage, /window\.open\(account\.task_open_url/, "任务页不能直开后端返回的 API URL，否则网页登录 token 不会随 window.open 携带");
assert.match(abilityWorkbench, /window\.open\("about:blank", "_blank"\)/, "工作台需要先打开空白窗口避免异步请求后被浏览器拦截");
assert.match(abilityWorkbench, /popup\.opener\s*=\s*null/, "工作台打开 about:blank 后必须断开 opener，避免新窗口反向控制平台页");

console.log("auth_ux_static_check_ok=true");
