param(
  [string]$Version = '0.9.1',
  [string]$HelperSourceRoot = '',
  [string]$LauncherSourcePath = '',
  [string]$InstallerSourcePath = '',
  [string]$ExtensionSourceRoot = '',
  [string]$OutputRoot = '',
  [string]$PlatformBaseUrl = 'http://192.168.10.149:8789',
  [string]$CodeSigningCertSubject = 'CN=AIDP Local Helper Code Signing',
  [switch]$TrustCodeSigningCertificate,
  [switch]$SkipCodeSigning
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

function Get-CSharpCompilerPath {
  $candidates = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
  )
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) { return $candidate }
  }
  $command = Get-Command csc.exe -ErrorAction SilentlyContinue
  if ($command -and $command.Source) { return $command.Source }
  throw '未找到 C# 编译器 csc.exe，无法生成 AIDP 本机助手.exe。'
}

function Build-WindowsLauncher {
  param([string]$Source, [string]$Destination)
  if (-not (Test-Path -LiteralPath $Source)) {
    throw "Windows launcher source not found: $Source"
  }
  $parent = Split-Path -Parent $Destination
  if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  $csc = Get-CSharpCompilerPath
  $args = @(
    '/nologo',
    '/target:winexe',
    '/platform:anycpu',
    '/optimize+',
    '/codepage:65001',
    '/reference:System.dll',
    '/reference:System.Drawing.dll',
    '/reference:System.Management.dll',
    '/reference:System.Windows.Forms.dll',
    ('/out:' + $Destination),
    $Source
  )
  & $csc @args
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Destination)) {
    throw "Failed to compile Windows launcher: $Source"
  }
}

function Build-WindowsInstaller {
  param([string]$Source, [string]$Destination, [string]$PayloadZip)
  if (-not (Test-Path -LiteralPath $Source)) {
    throw "Windows installer source not found: $Source"
  }
  if (-not (Test-Path -LiteralPath $PayloadZip)) {
    throw "Windows installer payload not found: $PayloadZip"
  }
  $parent = Split-Path -Parent $Destination
  if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  $baseExe = Join-Path $parent ('setup-base-' + [Guid]::NewGuid().ToString('N') + '.exe')
  $csc = Get-CSharpCompilerPath
  $args = @(
    '/nologo',
    '/target:winexe',
    '/platform:anycpu',
    '/optimize+',
    '/codepage:65001',
    '/reference:System.dll',
    '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll',
    '/reference:System.IO.Compression.dll',
    '/reference:System.IO.Compression.FileSystem.dll',
    ('/out:' + $baseExe),
    $Source
  )
  & $csc @args
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $baseExe)) {
    throw "Failed to compile Windows installer: $Source"
  }

  $marker = [System.Text.Encoding]::ASCII.GetBytes('AIDP_SETUP_PAYLOAD_V1')
  $payload = [System.IO.File]::ReadAllBytes($PayloadZip)
  $lengthBytes = [System.BitConverter]::GetBytes([Int64]$payload.Length)
  $base = [System.IO.File]::ReadAllBytes($baseExe)
  $stream = [System.IO.File]::Open($Destination, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
  try {
    $stream.Write($base, 0, $base.Length)
    $stream.Write($payload, 0, $payload.Length)
    $stream.Write($marker, 0, $marker.Length)
    $stream.Write($lengthBytes, 0, $lengthBytes.Length)
  } finally {
    $stream.Dispose()
    Remove-Item -LiteralPath $baseExe -Force -ErrorAction SilentlyContinue
  }
  if (-not (Test-Path -LiteralPath $Destination)) {
    throw "Failed to create Windows installer: $Destination"
  }
}

function Get-OrCreate-CodeSigningCertificate {
  param([string]$Subject)
  $now = Get-Date
  $cert = @(Get-ChildItem -Path Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -eq $Subject -and $_.HasPrivateKey -and $_.NotAfter -gt $now.AddDays(30) } |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1)[0]
  if (-not $cert) {
    $cert = New-SelfSignedCertificate `
      -Type CodeSigningCert `
      -Subject $Subject `
      -FriendlyName 'AIDP Local Helper Code Signing' `
      -CertStoreLocation Cert:\CurrentUser\My `
      -KeyAlgorithm RSA `
      -KeyLength 3072 `
      -HashAlgorithm SHA256 `
      -KeyUsage DigitalSignature `
      -NotAfter $now.AddYears(5)
  }
  $cert
}

function Trust-CodeSigningCertificate {
  param([System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate)
  $rootStorePath = 'Cert:\CurrentUser\Root'
  $publisherStorePath = 'Cert:\CurrentUser\TrustedPublisher'
  Add-CodeSigningCertificateToStore -Certificate $Certificate -StoreName 'Root' -StorePath $rootStorePath
  Add-CodeSigningCertificateToStore -Certificate $Certificate -StoreName 'TrustedPublisher' -StorePath $publisherStorePath
}

function Add-CodeSigningCertificateToStore {
  param(
    [System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate,
    [string]$StoreName,
    [string]$StorePath
  )
  $store = [System.Security.Cryptography.X509Certificates.X509Store]::new($StoreName, [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser)
  try {
    $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    $exists = $false
    foreach ($item in @($store.Certificates)) {
      if ($item.Thumbprint -eq $Certificate.Thumbprint) {
        $exists = $true
        break
      }
    }
    if (-not $exists) {
      $store.Add($Certificate)
    }
  } catch {
    throw "Failed to add code signing certificate to ${StorePath}: $($_.Exception.Message)"
  } finally {
    $store.Close()
  }
}

function Export-CodeSigningCertificate {
  param([System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate, [string]$Destination)
  $parent = Split-Path -Parent $Destination
  if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }
  Export-Certificate -Cert $Certificate -FilePath $Destination -Force | Out-Null
}

function Test-CodeSigningCertificateTrusted {
  param([System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate)
  $rootStore = [System.Security.Cryptography.X509Certificates.X509Store]::new('Root', [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser)
  $publisherStore = [System.Security.Cryptography.X509Certificates.X509Store]::new('TrustedPublisher', [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser)
  try {
    $rootStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
    $publisherStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
    $rootTrusted = $false
    foreach ($item in @($rootStore.Certificates)) {
      if ($item.Thumbprint -eq $Certificate.Thumbprint) {
        $rootTrusted = $true
        break
      }
    }
    $publisherTrusted = $false
    foreach ($item in @($publisherStore.Certificates)) {
      if ($item.Thumbprint -eq $Certificate.Thumbprint) {
        $publisherTrusted = $true
        break
      }
    }
    return ($rootTrusted -and $publisherTrusted)
  } finally {
    $rootStore.Close()
    $publisherStore.Close()
  }
}

function Sign-WindowsBinary {
  param([string]$Path, [System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate)
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Cannot sign missing file: $Path"
  }
  Set-AuthenticodeSignature -FilePath $Path -Certificate $Certificate -HashAlgorithm SHA256 | Out-Null
  $verify = Get-AuthenticodeSignature -FilePath $Path
  if (-not $verify.SignerCertificate) {
    throw "Failed to sign Windows binary: $Path; status=$($verify.Status); message=$($verify.StatusMessage)"
  }
  $verify
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
if (-not $LauncherSourcePath) {
  $LauncherSourcePath = Join-Path $repoRoot 'local-agent-launcher\AidpLocalHelperLauncher.cs'
}
if (-not $InstallerSourcePath) {
  $InstallerSourcePath = Join-Path $repoRoot 'local-agent-launcher\AidpLocalHelperSetup.cs'
}
if (-not $OutputRoot) {
  $OutputRoot = Join-Path $repoRoot 'data\local-agent\releases\packages'
}

$helperRoot = (Resolve-Path -LiteralPath $HelperSourceRoot).Path
$launcherSource = (Resolve-Path -LiteralPath $LauncherSourcePath).Path
$installerSource = (Resolve-Path -LiteralPath $InstallerSourcePath).Path
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

  $codeSigningCert = $null
  $codeSigningCertRelativePath = 'code-signing/AIDP-Local-Helper-CodeSigning.cer'
  if (-not $SkipCodeSigning) {
    $codeSigningCert = Get-OrCreate-CodeSigningCertificate -Subject $CodeSigningCertSubject
    if ($TrustCodeSigningCertificate) {
      Trust-CodeSigningCertificate -Certificate $codeSigningCert
    }
    Export-CodeSigningCertificate -Certificate $codeSigningCert -Destination (Join-Path $stagingRoot $codeSigningCertRelativePath)
  }

  $launcherExeName = 'AIDP 本机助手.exe'
  $launcherExePath = Join-Path $stagingRoot $launcherExeName
  Build-WindowsLauncher -Source $launcherSource -Destination $launcherExePath
  $launcherSignature = $null
  if ($codeSigningCert) {
    $launcherSignature = Sign-WindowsBinary -Path $launcherExePath -Certificate $codeSigningCert
  }

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
$sourceLauncher = Join-Path $suiteRoot 'AIDP 本机助手.exe'
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
$agentTarget = Join-Path $InstallRoot 'local-agent'
New-Item -ItemType Directory -Force -Path $agentTarget | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceAgent '*') -Destination $agentTarget -Recurse -Force
Copy-Item -LiteralPath $sourceLauncher -Destination (Join-Path $InstallRoot 'AIDP 本机助手.exe') -Force
Copy-Item -LiteralPath (Join-Path $suiteRoot 'manifest.json') -Destination (Join-Path $InstallRoot 'manifest.json') -Force
$extensionTarget = Join-Path $InstallRoot 'browser-extension'
New-Item -ItemType Directory -Force -Path $extensionTarget | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceExtension '*') -Destination $extensionTarget -Recurse -Force
[ordered]@{
  ok = $true
  install_root = $InstallRoot
  start_command = "`"$InstallRoot\AIDP 本机助手.exe`""
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

推荐直接运行套件根目录的安装包：

```powershell
.\AIDP-Local-Helper-Setup-$Version.exe
```

也可以使用脚本安装：

```powershell
pwsh.exe -ExecutionPolicy Bypass -File .\install.ps1
```

## 启动

```powershell
& "`$env:LOCALAPPDATA\AIDP\local-agent\AIDP 本机助手.exe"
```

## 插件

浏览器插件包位于安装目录的 `browser-extension` 子目录。P0 不静默替换插件，请在浏览器扩展页手动更新。
"@
  Write-Utf8File -Path (Join-Path $installRoot 'README.md') -Content ($installReadme.TrimStart() + "`n")

  $installerExeName = "AIDP-Local-Helper-Setup-$Version.exe"
  $manifest = [ordered]@{
    suite_version = $Version
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    code_signing = [ordered]@{
      mode = if ($codeSigningCert) { 'self_signed_internal' } else { 'unsigned' }
      subject = if ($codeSigningCert) { [string]$codeSigningCert.Subject } else { '' }
      thumbprint = if ($codeSigningCert) { [string]$codeSigningCert.Thumbprint } else { '' }
      certificate_path = if ($codeSigningCert) { $codeSigningCertRelativePath } else { '' }
      trusted_current_user = if ($codeSigningCert) { [bool](Test-CodeSigningCertificateTrusted -Certificate $codeSigningCert) } else { $false }
      trust_parameter = 'TrustCodeSigningCertificate'
    }
    windows_launcher = [ordered]@{
      version = $Version
      path = $launcherExeName
      signed = [bool]$codeSigningCert
      signature_status = if ($launcherSignature) { [string]$launcherSignature.Status } else { 'Unsigned' }
      tray = $true
      single_instance = $true
      autostart_entry = 'AIDP 本机助手.cmd'
    }
    windows_installer = [ordered]@{
      version = $Version
      path = $installerExeName
      signed = [bool]$codeSigningCert
      embedded_suite = $true
      supports_uninstall = $true
      creates_desktop_shortcut = $true
      creates_start_menu_shortcut = $true
      supports_autostart = $true
    }
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

  $payloadSuitePath = Join-Path $tempRoot "aidp-local-suite-$Version-payload.zip"
  New-ZipFromDirectory -SourceDirectory $stagingRoot -DestinationZip $payloadSuitePath

  $installerPath = Join-Path $outputRootResolved $installerExeName
  Build-WindowsInstaller -Source $installerSource -Destination $installerPath -PayloadZip $payloadSuitePath
  $installerSignature = $null
  if ($codeSigningCert) {
    $installerSignature = Sign-WindowsBinary -Path $installerPath -Certificate $codeSigningCert
  }
  Copy-RequiredFile -Source $installerPath -Destination (Join-Path $stagingRoot $installerExeName)

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
    installer = $installerPath
    local_agent = $agentPackagePath
    browser_extension = $extensionPackagePath
  } | ConvertTo-Json -Depth 20
} finally {
  if ($tempRoot -and (Test-Path -LiteralPath $tempRoot)) {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}
