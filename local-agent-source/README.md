# AIDP 本机助手

用途：让远程/NAS 平台通过当前电脑完成浏览器插件桥接、学习包上传、执行能力注册和本机诊断。

## 一键使用

推荐方式：

1. 下载 `AIDP-Local-Helper-Setup-0.9.1.exe`。
2. 双击安装，按中文安装向导选择安装位置、桌面快捷方式、开始菜单和开机自启动。
3. 安装完成后打开 `AIDP 本机助手`。
4. 在控制台选择或填写平台地址，默认 NAS 地址为 `http://192.168.10.149:8789`。
5. 如果平台开启 API Token 鉴权，在“连接设置”里填写平台 API Token。
6. 按页面提示安装浏览器插件、开启执行能力、设置开机自启动。

便携方式：

1. 下载并解压 `aidp-local-suite-0.9.1.zip`。
2. 双击 `AIDP 本机助手.exe`。
3. 本机助手会自动启动并打开 `http://127.0.0.1:8790` 中文控制台。

## 说明

- 本机助手监听：`http://127.0.0.1:8790`。
- `AIDP 本机助手.exe` 是普通用户入口；PowerShell 脚本只作为内部启动实现。
- 托盘菜单可打开控制台、测试平台连接、检查更新、重启或退出本机助手。
- 安装包会创建桌面快捷方式、开始菜单入口和卸载入口。
- 开机自启动会写入当前用户 Windows 启动文件夹，不需要管理员权限。
- 不需要管理员权限。
- 如果浏览器或安全软件拦截本地访问，请允许网页访问 `127.0.0.1:8790`。
- 如需卸载，可运行套件里的 `install/uninstall.ps1`，或删除安装目录和 Windows 启动文件夹中的 `AIDP 本机助手.cmd`。


## AI 相似度评分配置

- 本机助手 0.3.6 起提供本地 AI 代理：http://127.0.0.1:8790/api/ai-score/*，支持视觉低细节请求，默认不返回上游 raw 响应，并增强断连响应容错。
- 远程电脑需要 Windows PowerShell 5.1+ 或 PowerShell 7+；如果只有 PowerShell v1，会显示英文升级提示，不能直接运行。
- API Key 只放在本机环境变量，不写入浏览器扩展或 NAS。
- 默认 Base URL：http://api.51gugu.uk/v1。
- 默认模型：gpt-5.4-mini。
- 需要设置环境变量：AIDP_AI_API_KEY 或 OPENAI_API_KEY；可选覆盖：AIDP_AI_BASE_URL、AIDP_AI_MODEL。
- 健康检查会返回 aiScoreSupported、aiScoreConfigured、aiScoreModel。

## 日志与排障

- 本机助手提供 `http://127.0.0.1:8790/api/logs?limit=100` 和 `/api/diagnostics?limit=100`，返回最近助手运行日志。
- 日志会同时写入本机助手目录下的 `logs/helper-YYYY-MM-DD.jsonl`，便于排查 AI 慢请求、连接断开和 GetResponse 异常。
- AI 评分请求会记录模型、图片数量、请求体大小、耗时、重试和错误分类。

## 学习包上传链路

- 评分插件的操作录制只连接本机助手：`http://127.0.0.1:8790/api/recordings/upload`。
- helper 收到学习包后，会按 `platform_base_url + platform_api_prefix` 转发到平台 `/operation-recordings`。
- `platform_base_url` 可来自环境变量 `AIDP_PLATFORM_BASE_URL`，也可写在 `config/helper-settings.json`。
- `platform_api_token` 可来自环境变量 `AIDP_PLATFORM_API_TOKEN` / `AIDP_BROWSER_EXTENSION_API_TOKEN`，也可在控制台保存到 `config/helper-settings.json`：

```json
{
  "platform_base_url": "http://127.0.0.1:8789",
  "platform_api_token": "your-token",
  "recording_upload_retry_count": 2,
  "recording_upload_timeout_sec": 20
}
```

- Token 只用于请求头 `X-AIDP-API-Token`，控制台配置响应和技术日志不会回显明文。
- 当平台暂时不可达时，helper 会把学习包写入本地 `queue/operation-recordings/pending/`，并把失败快照写入 `queue/operation-recordings/failed/`。
- 后续新的学习包上传会自动先重试 pending 队列；也可手动调用 `http://127.0.0.1:8790/api/recordings/retry-pending`。
- `http://127.0.0.1:8790/api/health` 现在会返回 `platformBaseUrl`、`recordingUploadQueuePending`、`recordingUploadFailedCache` 等字段，便于现场排障。


## 当前项目默认配置

- 发布包不内置 API Key；请在本机环境变量中设置 `AIDP_AI_API_KEY` 或 `OPENAI_API_KEY`。
- 默认 `AIDP_AI_BASE_URL=http://api.51gugu.uk/v1`、`AIDP_AI_MODEL=gpt-5.4-mini`；如需更换，设置环境变量覆盖。


