param(
  [string]$Version = '0.9.0',
  [string]$HelperSourceRoot = '',
  [string]$ExtensionSourceRoot = '',
  [string]$OutputRoot = '',
  [string]$PlatformBaseUrl = 'http://192.168.10.149:8789'
)

$ErrorActionPreference = 'Stop'

function New-Utf8NoBomEncoding {
  [System.Text.UTF8Encoding]::new($false)
}

function Write-Utf8File {
  param([string]$Path, [string]$Content)
  $parent = Split-Path -Parent $Path
  if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  [System.IO.File]::WriteAllText($Path, $Content, (New-Utf8NoBomEncoding))
}

function Copy-RequiredFile {
  param([string]$Source, [string]$Destination)
  if (-not (Test-Path -LiteralPath $Source)) {
    throw "Required source file not found: $Source"
  }
  $parent = Split-Path -Parent $Destination
  if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  Copy-Item -LiteralPath $Source -Destination $Destination -Force
}

function New-ZipFromDirectory {
  param([string]$SourceDirectory, [string]$DestinationZip)
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  if (Test-Path -LiteralPath $DestinationZip) {
    Remove-Item -LiteralPath $DestinationZip -Force
  }
  $parent = Split-Path -Parent $DestinationZip
  if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  $sourceRoot = (Resolve-Path -LiteralPath $SourceDirectory).Path
  $zip = [System.IO.Compression.ZipFile]::Open($DestinationZip, [System.IO.Compression.ZipArchiveMode]::Create)
  try {
    foreach ($file in Get-ChildItem -LiteralPath $sourceRoot -Recurse -File) {
      $relative = [System.IO.Path]::GetRelativePath($sourceRoot, $file.FullName).Replace('\', '/')
      [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, $relative) | Out-Null
    }
  } finally {
    $zip.Dispose()
  }
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$projectsRoot = Split-Path -Parent $repoRoot
if (-not $HelperSourceRoot) {
  $repoHelperSourceRoot = Join-Path $repoRoot 'local-agent-source'
  if (Test-Path -LiteralPath (Join-Path $repoHelperSourceRoot 'host-launcher.ps1')) {
    $HelperSourceRoot = $repoHelperSourceRoot
  } else {
    $HelperSourceRoot = Join-Path $projectsRoot 'aidp-monitor\tools\local-helper-package'
  }
}
if (-not $ExtensionSourceRoot) {
  $ExtensionSourceRoot = Join-Path $projectsRoot 'aidp-monitor\browser-extension\aidp-score-helper'
}
if (-not $OutputRoot) {
  $OutputRoot = Join-Path $repoRoot 'data\local-agent\releases\packages'
}

$helperRoot = (Resolve-Path -LiteralPath $HelperSourceRoot).Path
$extensionRoot = (Resolve-Path -LiteralPath $ExtensionSourceRoot).Path
if (-not (Test-Path -LiteralPath $OutputRoot)) {
  New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
}
$outputRootResolved = (Resolve-Path -LiteralPath $OutputRoot).Path

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('aidp-local-suite-' + [Guid]::NewGuid().ToString('N'))
$stagingRoot = Join-Path $tempRoot "aidp-local-suite-$Version"
$localAgentRoot = Join-Path $stagingRoot 'local-agent'
$extensionStageRoot = Join-Path $stagingRoot 'browser-extension'
$installRoot = Join-Path $stagingRoot 'install'

try {
  New-Item -ItemType Directory -Force -Path $localAgentRoot, $extensionStageRoot, $installRoot | Out-Null

  Copy-RequiredFile -Source (Join-Path $helperRoot 'host-launcher.ps1') -Destination (Join-Path $localAgentRoot 'host-launcher.ps1')
  $helperReadme = Join-Path $helperRoot 'README.md'
  if (Test-Path -LiteralPath $helperReadme) {
    Copy-RequiredFile -Source $helperReadme -Destination (Join-Path $localAgentRoot 'README.md')
  } else {
    Write-Utf8File -Path (Join-Path $localAgentRoot 'README.md') -Content "# AIDP Local Agent`n`n启动 local-agent/host-launcher.ps1。`n"
  }

  $platformUrls = @(
    [ordered]@{
      id = 'local-dev'
      name = '本地开发地址'
      url = 'http://127.0.0.1:8789'
      is_builtin = $true
    },
    [ordered]@{
      id = 'nas-lan'
      name = 'NAS 局域网地址'
      url = 'http://192.168.10.149:8789'
      is_builtin = $true
    },
    [ordered]@{
      id = 'public-domain'
      name = '公网访问地址'
      url = 'https://platform.51gugu.uk'
      is_builtin = $true
    }
  )
  $normalizedPlatformBaseUrl = ([string]$PlatformBaseUrl).Trim().TrimEnd('/')
  $activePlatformUrl = @($platformUrls | Where-Object { ([string]$_['url']).TrimEnd('/') -eq $normalizedPlatformBaseUrl } | Select-Object -First 1)[0]
  if ($activePlatformUrl) {
    $activePlatformUrlId = [string]$activePlatformUrl['id']
  } else {
    $activePlatformUrlId = 'custom-default'
    $platformUrls += ,[ordered]@{
      id = $activePlatformUrlId
      name = '自定义默认地址'
      url = $normalizedPlatformBaseUrl
      is_builtin = $false
    }
  }

  $defaultConfig = [ordered]@{
    platform_base_url = $normalizedPlatformBaseUrl
    active_platform_url_id = $activePlatformUrlId
    platform_urls = @($platformUrls)
    agent_port = 8790
    worker_runtime_enabled = $true
    plugin_bridge_enabled = $true
    upload_queue_enabled = $true
    auto_update_enabled = $true
  }
  Write-Utf8File -Path (Join-Path $localAgentRoot 'config\default-config.json') -Content (($defaultConfig | ConvertTo-Json -Depth 20) + "`n")

  $startScript = @'
param(
  [int]$Port = 8790,
  [string]$HostName = '127.0.0.1',
  [switch]$AutoOpenAccounts
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$helper = Join-Path $scriptRoot 'host-launcher.ps1'
$argsList = @('-Port', $Port, '-HostName', $HostName)
if ($AutoOpenAccounts) { $argsList += '-AutoOpenAccounts' }
& $helper @argsList
'@
  Write-Utf8File -Path (Join-Path $localAgentRoot 'start-local-agent.ps1') -Content ($startScript.TrimStart() + "`n")

  $applyUpdateScript = @'
param(
  [Parameter(Mandatory = $true)]
  [string]$UpdateZip
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $UpdateZip)) {
  throw "Update package not found: $UpdateZip"
}
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$extractRoot = Join-Path $scriptRoot '_update_extract'
if (Test-Path -LiteralPath $extractRoot) {
  Remove-Item -LiteralPath $extractRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $extractRoot | Out-Null
Expand-Archive -LiteralPath $UpdateZip -DestinationPath $extractRoot -Force
[ordered]@{
  ok = $true
  status = 'downloaded'
  extract_root = $extractRoot
  message = '更新包已解压；请在本机助手空闲时替换文件。'
} | ConvertTo-Json -Depth 10
'@
  Write-Utf8File -Path (Join-Path $localAgentRoot 'apply-update.ps1') -Content ($applyUpdateScript.TrimStart() + "`n")

  $extensionZip = Join-Path $extensionStageRoot "aidp-score-helper-$Version.zip"
  New-ZipFromDirectory -SourceDirectory $extensionRoot -DestinationZip $extensionZip
  $extensionReadme = Join-Path $extensionRoot 'README.md'
  if (Test-Path -LiteralPath $extensionReadme) {
    Copy-RequiredFile -Source $extensionReadme -Destination (Join-Path $extensionStageRoot 'README.md')
  } else {
    Write-Utf8File -Path (Join-Path $extensionStageRoot 'README.md') -Content "# AIDP Browser Extension`n`n在浏览器扩展管理页手动加载或更新插件包。`n"
  }

  $installScript = @'
param(
  [string]$InstallRoot = "$env:LOCALAPPDATA\AIDP\local-agent"
)

$ErrorActionPreference = 'Stop'
$suiteRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$sourceAgent = Join-Path $suiteRoot 'local-agent'
$sourceExtension = Join-Path $suiteRoot 'browser-extension'
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceAgent '*') -Destination $InstallRoot -Recurse -Force
$extensionTarget = Join-Path $InstallRoot 'browser-extension'
New-Item -ItemType Directory -Force -Path $extensionTarget | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceExtension '*') -Destination $extensionTarget -Recurse -Force
[ordered]@{
  ok = $true
  install_root = $InstallRoot
  start_command = "pwsh.exe -ExecutionPolicy Bypass -File `"$InstallRoot\start-local-agent.ps1`""
  message = '本机助手已安装；插件包已复制到 browser-extension 目录，请按 README 手动更新浏览器插件。'
} | ConvertTo-Json -Depth 10
'@
  Write-Utf8File -Path (Join-Path $installRoot 'install.ps1') -Content ($installScript.TrimStart() + "`n")

  $uninstallScript = @'
param(
  [string]$InstallRoot = "$env:LOCALAPPDATA\AIDP\local-agent"
)

$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $InstallRoot) {
  Remove-Item -LiteralPath $InstallRoot -Recurse -Force
}
[ordered]@{
  ok = $true
  install_root = $InstallRoot
  message = '本机助手已卸载。'
} | ConvertTo-Json -Depth 10
'@
  Write-Utf8File -Path (Join-Path $installRoot 'uninstall.ps1') -Content ($uninstallScript.TrimStart() + "`n")

  $installReadme = @"
# AIDP 本机助手套件

## 安装

```powershell
pwsh.exe -ExecutionPolicy Bypass -File .\install.ps1
```

## 启动

```powershell
pwsh.exe -ExecutionPolicy Bypass -File `"`$env:LOCALAPPDATA\AIDP\local-agent\start-local-agent.ps1`"
```

## 插件

浏览器插件包位于安装目录的 `browser-extension` 子目录。P0 不静默替换插件，请在浏览器扩展页手动更新。
"@
  Write-Utf8File -Path (Join-Path $installRoot 'README.md') -Content ($installReadme.TrimStart() + "`n")

  $manifest = [ordered]@{
    suite_version = $Version
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    local_agent = [ordered]@{
      version = $Version
      path = 'local-agent/'
      entry = 'local-agent/host-launcher.ps1'
    }
    browser_extension = [ordered]@{
      version = $Version
      path = "browser-extension/aidp-score-helper-$Version.zip"
    }
    install = [ordered]@{
      entry = 'install/install.ps1'
      uninstall = 'install/uninstall.ps1'
    }
  }
  Write-Utf8File -Path (Join-Path $stagingRoot 'manifest.json') -Content (($manifest | ConvertTo-Json -Depth 20) + "`n")

  $suitePath = Join-Path $outputRootResolved "aidp-local-suite-$Version.zip"
  New-ZipFromDirectory -SourceDirectory $stagingRoot -DestinationZip $suitePath

  $agentPackagePath = Join-Path $outputRootResolved 'aidp-local-helper.zip'
  New-ZipFromDirectory -SourceDirectory $localAgentRoot -DestinationZip $agentPackagePath

  $extensionPackagePath = Join-Path $outputRootResolved "aidp-score-helper-$Version.zip"
  Copy-Item -LiteralPath $extensionZip -Destination $extensionPackagePath -Force

  [ordered]@{
    ok = $true
    version = $Version
    suite = $suitePath
    local_agent = $agentPackagePath
    browser_extension = $extensionPackagePath
  } | ConvertTo-Json -Depth 20
} finally {
  if ($tempRoot -and (Test-Path -LiteralPath $tempRoot)) {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}
