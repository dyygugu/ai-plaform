# AIDP 本机同步助手

用途：让远程/NAS 面板可以自动打开本机独立 Edge profile，用于登录新的 AIDP 账号，避免手动拷贝脚本或误用旧登录态。

## 一键使用

1. 解压本 zip 到任意目录，例如桌面。
2. 优先双击英文入口 `install-and-start-aidp-helper.cmd`；中文入口 `安装并启动AIDP本机助手.cmd` 只是兼容入口。
3. 回到 AIDP 面板刷新页面。
4. 点击“新账号登录”，面板会自动调用本机助手打开独立 Edge profile。
5. 在新窗口登录 AIDP 后，用 `aidp-token-sync` 扩展同步当前登录态。

## 说明

- 本机助手监听：`http://127.0.0.1:8790`。
- 安装脚本会写入 Windows 启动文件夹，之后开机自动运行。
- 不需要管理员权限。
- 如果浏览器或安全软件拦截本地访问，请允许网页访问 `127.0.0.1:8790`。
- 如需卸载，删除 Windows 启动文件夹中的 `AIDP-Local-Helper.cmd`，并关闭对应 PowerShell 窗口即可。


## AI 相似度评分配置

- 本机助手 0.3.6 起提供本地 AI 代理：http://127.0.0.1:8790/api/ai-score/*，支持视觉低细节请求，默认不返回上游 raw 响应，并增强断连响应容错。
- 远程电脑需要 Windows PowerShell 5.1+ 或 PowerShell 7+；如果只有 PowerShell v1，会显示英文升级提示，不能直接运行。
- API Key 只放在本机环境变量，不写入浏览器扩展或 NAS。
- 默认 Base URL：http://api.51gugu.uk/v1。
- 默认模型：gpt-5.4-mini。
- 需要设置环境变量：AIDP_AI_API_KEY；可选覆盖：AIDP_AI_BASE_URL、AIDP_AI_MODEL。
- 健康检查会返回 aiScoreSupported、aiScoreConfigured、aiScoreModel。

## 日志与排障

- 本机助手提供 `http://127.0.0.1:8790/api/logs?limit=100` 和 `/api/diagnostics?limit=100`，返回最近助手运行日志。
- 日志会同时写入本机助手目录下的 `logs/helper-YYYY-MM-DD.jsonl`，便于排查 AI 慢请求、连接断开和 GetResponse 异常。
- AI 评分请求会记录模型、图片数量、请求体大小、耗时、重试和错误分类。

## 学习包上传链路

- 评分插件的操作录制只连接本机助手：`http://127.0.0.1:8790/api/recordings/upload`。
- helper 收到学习包后，会按 `platform_base_url` 转发到平台 `/api/v1/operation-recordings`。
- `platform_base_url` 可来自环境变量 `AIDP_PLATFORM_BASE_URL`，也可写在 `config/helper-settings.json`：

```json
{
  "platform_base_url": "http://127.0.0.1:8789",
  "recording_upload_retry_count": 2,
  "recording_upload_timeout_sec": 20
}
```

- 当平台暂时不可达时，helper 会把学习包写入本地 `queue/operation-recordings/pending/`，并把失败快照写入 `queue/operation-recordings/failed/`。
- 后续新的学习包上传会自动先重试 pending 队列；也可手动调用 `http://127.0.0.1:8790/api/recordings/retry-pending`。
- `http://127.0.0.1:8790/api/health` 现在会返回 `platformBaseUrl`、`recordingUploadQueuePending`、`recordingUploadFailedCache` 等字段，便于现场排障。


## 当前项目内置配置

- 本项目个人版已内置默认 `AIDP_AI_API_KEY`、`AIDP_AI_BASE_URL=http://api.51gugu.uk/v1`、`AIDP_AI_MODEL=gpt-5.4-mini`。
- 环境变量仍有最高优先级；如果以后更换 key，只要设置环境变量即可覆盖内置值。


