param(
  [int]$Port = 8790,
  [string]$HostName = '127.0.0.1',
  [switch]$AutoOpenAccounts
)
$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:HelperVersion = '0.9.1'
$script:HelperStartedAt = (Get-Date).ToUniversalTime().AddHours(8).ToString('s')
$script:InjectedProfilePorts = @{}
$script:HelperLogs = @()
$script:WorkerRuntimeJob = $null
$script:WorkerRuntimeCurrentCommandId = ''
$script:UpdateInProgress = $false
try {
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {}

$script:PowerShellMajor = if (Test-Path variable:PSVersionTable) { [int]$PSVersionTable.PSVersion.Major } else { 1 }
if ($script:PowerShellMajor -lt 5) {
  Write-Host 'AIDP Local Helper requires Windows PowerShell 5.1+ or PowerShell 7+.'
  Write-Host 'This computer is running PowerShell v1/v2/v3/v4, which cannot run the helper.'
  Write-Host 'Please install Windows Management Framework 5.1 or PowerShell 7, then run install-and-start-aidp-helper.cmd again.'
  throw 'PowerShell version too old'
}

function New-Utf8NoBomEncoding { New-Object System.Text.UTF8Encoding($false) }

function Get-AidpWorkerEventErrorCode {
  param([string]$Event = '', [string]$Message = '')
  $text = (([string]$Event) + ' ' + ([string]$Message)).ToLowerInvariant()
  if ($text -match '502|bad gateway') { return 'AI_PROVIDER_502' }
  if ($text -match 'timeout|timed out|超时') { return 'AI_PROVIDER_TIMEOUT' }
  'WORKER_EXCEPTION'
}

function Invoke-AidpWorkerEventReport {
  param([string]$Level = 'info', [string]$Event = 'event', [string]$Message = '', $Data = $null)
  try {
    $severity = if ($Level -match '^(error|critical)$') { 'error' } elseif ($Level -match '^(warn|warning)$') { 'warning' } else { 'info' }
    $payload = [ordered]@{
      worker_id = 'aidp-local-helper'
      event_type = 'event_report'
      target_version = $script:HelperVersion
      severity = $severity
      stage = 'worker_runtime'
      step = 'log_summary'
      message = $Message
      error_detail = $Event
    }
    if ($severity -ne 'info') { $payload.error_code = Get-AidpWorkerEventErrorCode -Event $Event -Message $Message; $payload.retryable = $true }
    if ($null -ne $Data) { $payload.context = $Data }
    Invoke-PlatformApi -Method 'POST' -Path '/workers/events' -Payload $payload -TimeoutSec 2 | Out-Null
  } catch {}
}

function Get-HelperLogPath {
  $logDir = Join-Path $PSScriptRoot 'logs'
  if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
  Join-Path $logDir ("helper-{0}.jsonl" -f ((Get-Date).ToUniversalTime().AddHours(8).ToString('yyyy-MM-dd')))
}

function Add-HelperLog {
  param([string]$Level = 'info', [string]$Event = 'event', [string]$Message = '', $Data = $null)
  $entry = [ordered]@{
    at = (Get-Date).ToUniversalTime().AddHours(8).ToString('s')
    level = $Level
    event = $Event
    message = $Message
  }
  if ($null -ne $Data) { $entry.data = $Data }
  $script:HelperLogs += [pscustomobject]$entry
  if (@($script:HelperLogs).Count -gt 300) { $script:HelperLogs = @($script:HelperLogs | Select-Object -Last 300) }
  try {
    $json = $entry | ConvertTo-Json -Depth 20 -Compress
    [System.IO.File]::AppendAllText((Get-HelperLogPath), $json + [Environment]::NewLine, (New-Utf8NoBomEncoding))
  } catch {}
  if ($Level -match '^(warn|warning|error|critical)$' -or $Event -eq 'helper.started') {
    Invoke-AidpWorkerEventReport -Level $Level -Event $Event -Message $Message -Data $Data
  }
}

function Get-HelperLogs {
  param([int]$Limit = 100)
  $safeLimit = [Math]::Max(1, [Math]::Min(300, $Limit))
  [ordered]@{ ok = $true; logs = @($script:HelperLogs | Select-Object -Last $safeLimit); logPath = Get-HelperLogPath }
}

function ConvertTo-PlainHashtable {
  param($Value)
  if ($null -eq $Value) { return $null }
  if ($Value -is [string]) { return $Value }
  if ($Value -is [System.Collections.IDictionary]) {
    $result = @{}
    foreach ($key in $Value.Keys) { $result[$key] = ConvertTo-PlainHashtable $Value[$key] }
    return $result
  }
  if ($Value -is [System.Collections.IEnumerable] -and -not ($Value -is [string])) {
    $items = @()
    foreach ($item in $Value) { $items += ,(ConvertTo-PlainHashtable $item) }
    return $items
  }
  if ($Value.PSObject -and $Value.PSObject.Properties.Count -gt 0 -and $Value.GetType().FullName -eq 'System.Management.Automation.PSCustomObject') {
    $result = @{}
    foreach ($property in $Value.PSObject.Properties) { $result[$property.Name] = ConvertTo-PlainHashtable $property.Value }
    return $result
  }
  $Value
}

function Repair-DuplicateJsonHeaderKeys {
  param([string]$Json)
  $safeText = [string]$Json
  $headerNames = @(
    'Server-Timing',
    'Access-Control-Expose-Headers',
    'Access-Control-Allow-Origin',
    'Access-Control-Allow-Headers',
    'Access-Control-Allow-Methods',
    'Access-Control-Allow-Credentials',
    'Timing-Allow-Origin',
    'Content-Security-Policy'
  )
  foreach ($headerName in $headerNames) {
    $upperKey = ($headerName -replace '[^A-Za-z0-9]', '_')
    $lowerKey = ($headerName.ToLowerInvariant() -replace '[^A-Za-z0-9]', '_') + '_lower'
    $safeText = [regex]::Replace($safeText, '"' + [regex]::Escape($headerName.ToLowerInvariant()) + '"\s*:', '"' + $lowerKey + '":')
    $safeText = [regex]::Replace($safeText, '"' + [regex]::Escape($headerName) + '"\s*:', '"' + $upperKey + '":')
  }
  $safeText
}

function ConvertFrom-JsonCompat {
  param([string]$Json)
  $text = [string]$Json
  if (-not $text) { return $null }
  $text = Repair-DuplicateJsonHeaderKeys $text
  $convertCommand = Get-Command ConvertFrom-Json -ErrorAction SilentlyContinue
  $supportsAsHashtable = $convertCommand -and $convertCommand.Parameters.ContainsKey('AsHashtable')
  try {
    if ($supportsAsHashtable) { return ConvertTo-PlainHashtable ($text | ConvertFrom-Json -AsHashtable) }
    return ConvertTo-PlainHashtable ($text | ConvertFrom-Json)
  } catch {
    $message = [string]$_.Exception.Message
    if ($message -match 'duplicate|重复|same key|Server-Timing|server-timing|Access-Control') {
      $safeText = Repair-DuplicateJsonHeaderKeys $text
      if ($supportsAsHashtable) { return ConvertTo-PlainHashtable ($safeText | ConvertFrom-Json -AsHashtable) }
      return ConvertTo-PlainHashtable ($safeText | ConvertFrom-Json)
    }
    throw
  }
}

function ConvertTo-JsonString {
  param([string]$Value)
  return ($Value | ConvertTo-Json -Compress)
}

function Get-FreeTcpPort {
  param([int]$Start = 9350, [int]$End = 9450)
  $used = @{}
  try {
    foreach ($item in @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)) {
      $used[[int]$item.LocalPort] = $true
    }
  } catch {}
  for ($port = $Start; $port -le $End; $port++) {
    if (-not $used.ContainsKey($port)) { return $port }
  }
  throw "No free TCP port is available in $Start-$End."
}

function Get-MapValue {
  param($Map, [string]$Key)
  if ($null -eq $Map) { return $null }
  if ($Map -is [System.Collections.IDictionary]) { return $Map[$Key] }
  $property = $Map.PSObject.Properties[$Key]
  if ($property) { return $property.Value }
  $null
}

function Get-FirstChoice {
  param($Response)
  $choices = Get-MapValue $Response 'choices'
  if ($null -eq $choices) { return $null }
  if ($choices -is [System.Collections.IList] -or $choices -is [object[]]) {
    if ($choices.Count -gt 0) { return $choices[0] }
    return $null
  }
  $choices
}

function Write-JsonResponse {
  param($Response, $Data, [int]$StatusCode = 200)
  $json = $Data | ConvertTo-Json -Depth 100
  $bytes = [Text.Encoding]::UTF8.GetBytes($json)
  try {
    $Response.StatusCode = $StatusCode
    $Response.ContentType = 'application/json; charset=utf-8'
    $Response.ContentLength64 = $bytes.Length
    $Response.Headers['Access-Control-Allow-Origin'] = '*'
    $Response.Headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    $Response.Headers['Access-Control-Allow-Headers'] = 'content-type'
    $Response.Headers['Access-Control-Allow-Private-Network'] = 'true'
    $Response.OutputStream.Write($bytes, 0, $bytes.Length)
  } catch {
  } finally {
    try { $Response.OutputStream.Close() } catch {}
    try { $Response.Close() } catch {}
  }
}

function Write-HtmlResponse {
  param($Response, [string]$Html, [int]$StatusCode = 200)
  $bytes = [Text.Encoding]::UTF8.GetBytes($Html)
  try {
    $Response.StatusCode = $StatusCode
    $Response.ContentType = 'text/html; charset=utf-8'
    $Response.ContentLength64 = $bytes.Length
    $Response.Headers['Access-Control-Allow-Origin'] = '*'
    $Response.Headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    $Response.Headers['Access-Control-Allow-Headers'] = 'content-type'
    $Response.OutputStream.Write($bytes, 0, $bytes.Length)
  } catch {
  } finally {
    try { $Response.OutputStream.Close() } catch {}
    try { $Response.Close() } catch {}
  }
}

function Write-FileResponse {
  param($Response, [string]$Path, [string]$ContentType = 'application/octet-stream')
  try {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'File not found.' }
    $bytes = [IO.File]::ReadAllBytes($Path)
    $Response.StatusCode = 200
    $Response.ContentType = $ContentType
    $Response.ContentLength64 = $bytes.Length
    $Response.Headers['Access-Control-Allow-Origin'] = '*'
    $Response.OutputStream.Write($bytes, 0, $bytes.Length)
  } catch {
    Write-JsonResponse $Response ([ordered]@{ ok = $false; error = $_.Exception.Message }) 404
    return
  } finally {
    try { $Response.OutputStream.Close() } catch {}
    try { $Response.Close() } catch {}
  }
}

function Write-OptionsResponse {
  param($Response)
  try {
    $Response.StatusCode = 204
    $Response.ContentLength64 = 0
    $Response.Headers['Access-Control-Allow-Origin'] = '*'
    $Response.Headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    $Response.Headers['Access-Control-Allow-Headers'] = 'content-type'
    $Response.Headers['Access-Control-Allow-Private-Network'] = 'true'
  } finally {
    try { $Response.Close() } catch {}
  }
}

function Get-RequestBodyText {
  param($Request)
  if (-not $Request.HasEntityBody) { return '' }
  $stream = New-Object IO.MemoryStream
  try {
    $Request.InputStream.CopyTo($stream)
    $bytes = $stream.ToArray()
    if (-not $bytes -or $bytes.Length -eq 0) { return '' }
    $contentType = [string]$Request.ContentType
    $encoding = $null
    if ($contentType -match 'charset\s*=\s*([^;]+)') {
      try { $encoding = [Text.Encoding]::GetEncoding($matches[1].Trim().Trim('"')) } catch { $encoding = $null }
    }
    if (-not $encoding) { $encoding = [Text.Encoding]::UTF8 }
    return $encoding.GetString($bytes)
  } finally {
    $stream.Dispose()
  }
}

function Save-DataUrlImage {
  param([string]$DataUrl, [string]$PathWithoutExtension)
  if (-not $DataUrl) { return $null }
  if ($DataUrl -notmatch '^data:image/([a-zA-Z0-9.+-]+);base64,(.+)$') { return $null }
  $ext = $matches[1].ToLowerInvariant()
  if ($ext -eq 'jpeg') { $ext = 'jpg' }
  if ($ext -notmatch '^(jpg|png|webp)$') { $ext = 'img' }
  $bytes = [Convert]::FromBase64String($matches[2])
  $path = "$PathWithoutExtension.$ext"
  [IO.File]::WriteAllBytes($path, $bytes)
  [ordered]@{ path = $path; bytes = $bytes.Length }
}

function Save-AiScoreDebugScreenshots {
  param($Body)
  $root = Join-Path $PSScriptRoot 'debug\ai-screenshots'
  if (-not (Test-Path -LiteralPath $root)) { New-Item -ItemType Directory -Path $root -Force | Out-Null }
  $stamp = (Get-Date).ToString('yyyyMMdd-HHmmss-fff')
  $dir = Join-Path $root $stamp
  New-Item -ItemType Directory -Path $dir -Force | Out-Null
  $saved = @()
  $reference = Save-DataUrlImage -DataUrl ([string](Get-MapValue $Body 'referenceImage')) -PathWithoutExtension (Join-Path $dir 'reference')
  if ($reference) { $saved += ,([ordered]@{ name = 'reference'; path = $reference.path; url = "http://127.0.0.1:8790/api/ai-score/debug-screenshots/file?run=$stamp&file=reference$([IO.Path]::GetExtension($reference.path))"; bytes = $reference.bytes }) }
  $index = 0
  foreach ($image in @((Get-MapValue $Body 'modelImages'))) {
    $index += 1
    $dataUrl = if ($image -is [string]) { $image } else { [string](Get-MapValue $image 'image') }
    $modelIndex = if ($image -is [string]) { $index } else { [int]((Get-MapValue $image 'modelIndex') -as [int]) }
    if (-not $modelIndex) { $modelIndex = $index }
    $file = Save-DataUrlImage -DataUrl $dataUrl -PathWithoutExtension (Join-Path $dir (('model-{0:D2}' -f $modelIndex)))
    if ($file) { $saved += ,([ordered]@{ name = ('model-{0}' -f $modelIndex); path = $file.path; url = "http://127.0.0.1:8790/api/ai-score/debug-screenshots/file?run=$stamp&file=$([IO.Path]::GetFileName($file.path))"; bytes = $file.bytes }) }
  }
  $meta = [ordered]@{ ok = $true; savedAt = $stamp; directory = $dir; count = @($saved).Count; files = $saved }
  [IO.File]::WriteAllText((Join-Path $dir 'metadata.json'), ($meta | ConvertTo-Json -Depth 20), (New-Utf8NoBomEncoding))
  $meta
}

function Get-AiScoreConfig {
  $apiKey = if ($env:AIDP_AI_API_KEY) { [string]$env:AIDP_AI_API_KEY } else { 'sk-0Bp78XGfQKfW9atAZ' }
  $baseUrl = if ($env:AIDP_AI_BASE_URL) { [string]$env:AIDP_AI_BASE_URL } else { 'http://api.51gugu.uk/v1' }
  $model = if ($env:AIDP_AI_MODEL) { [string]$env:AIDP_AI_MODEL } else { 'gpt-5.4-mini' }
  [ordered]@{ configured = [bool]($apiKey -and $model); hasApiKey = [bool]$apiKey; baseUrl = $baseUrl; model = $model; provider = 'openai-compatible' }
}

function Invoke-JsonPostUtf8 {
  param([string]$Uri, [hashtable]$Headers, [string]$JsonBody, [int]$TimeoutSec = 180)
  $requestStartedAt = Get-Date
  $request = [Net.WebRequest]::Create($Uri)
  $request.Method = 'POST'
  $request.ContentType = 'application/json; charset=utf-8'
  $request.Accept = 'application/json'
  $request.Timeout = [Math]::Max(1, $TimeoutSec) * 1000
  $request.ReadWriteTimeout = [Math]::Max(1, $TimeoutSec) * 1000
  foreach ($key in @($Headers.Keys)) {
    if ($key -ieq 'Authorization') { $request.Headers['Authorization'] = [string]$Headers[$key] }
    elseif ($key -ieq 'Accept') { $request.Accept = [string]$Headers[$key] }
    elseif ($key -ieq 'Content-Type') { $request.ContentType = [string]$Headers[$key] }
    else { $request.Headers[[string]$key] = [string]$Headers[$key] }
  }
  $bytes = [Text.Encoding]::UTF8.GetBytes($JsonBody)
  $request.ContentLength = $bytes.Length
  $requestStream = $request.GetRequestStream()
  try { $requestStream.Write($bytes, 0, $bytes.Length) } finally { $requestStream.Dispose() }
  $response = $null
  try {
    $response = $request.GetResponse()
    $stream = $response.GetResponseStream()
    $memory = New-Object IO.MemoryStream
    try {
      $stream.CopyTo($memory)
      $text = [Text.Encoding]::UTF8.GetString($memory.ToArray())
      Add-HelperLog -Level 'info' -Event 'http.post.ok' -Message '上游 HTTP POST 成功' -Data ([ordered]@{ uri = $Uri; elapsedMs = [int]((Get-Date) - $requestStartedAt).TotalMilliseconds; bytes = $bytes.Length })
      return ConvertFrom-JsonCompat $text
    } finally {
      if ($stream) { $stream.Dispose() }
      $memory.Dispose()
    }
  } catch [Net.WebException] {
    $errorResponse = $_.Exception.Response
    if ($errorResponse) {
      $stream = $errorResponse.GetResponseStream()
      $memory = New-Object IO.MemoryStream
      try {
        if ($stream) { $stream.CopyTo($memory) }
        $text = [Text.Encoding]::UTF8.GetString($memory.ToArray())
        if ($text) {
          Add-HelperLog -Level 'warn' -Event 'http.post.error.response' -Message $text -Data ([ordered]@{ uri = $Uri; elapsedMs = [int]((Get-Date) - $requestStartedAt).TotalMilliseconds; bytes = $bytes.Length })
          throw $text
        }
      } finally {
        if ($stream) { $stream.Dispose() }
        $memory.Dispose()
      }
    }
    Add-HelperLog -Level 'warn' -Event 'http.post.webexception' -Message $_.Exception.Message -Data ([ordered]@{ uri = $Uri; elapsedMs = [int]((Get-Date) - $requestStartedAt).TotalMilliseconds; status = [string]$_.Exception.Status; bytes = $bytes.Length })
    throw
  } finally {
    if ($response) { $response.Close() }
  }
}

function New-AiScoreMessages {
  param($Body)
  if ($Body.messages) { return @($Body.messages) }
  $content = @()
  $prompt = if ($Body.prompt) { [string]$Body.prompt } else { 'Use AIDP AI similarity scoring rules. Compare the reference image and 7 model page screenshots. Return strict JSON only.' }
  $content += [ordered]@{ type = 'text'; text = $prompt }
  if ($Body.rubric) { $content += [ordered]@{ type = 'text'; text = "Scoring rubric:`n$($Body.rubric | ConvertTo-Json -Depth 80 -Compress)" } }
  if ($Body.calibrationExamples) { $content += [ordered]@{ type = 'text'; text = "Calibration examples:`n$($Body.calibrationExamples | ConvertTo-Json -Depth 80 -Compress)" } }
  $imageDetail = if ($Body.imageDetail) { [string]$Body.imageDetail } else { 'low' }
  function New-ImageUrlPart {
    param([string]$Url)
    $imageUrl = [ordered]@{ url = $Url }
    if ($imageDetail -and $imageDetail -ne 'auto') { $imageUrl.detail = $imageDetail }
    [ordered]@{ type = 'image_url'; image_url = $imageUrl }
  }
  if ($Body.referenceImage) {
    $content += [ordered]@{ type = 'text'; text = 'Reference/original page screenshot:' }
    $content += New-ImageUrlPart -Url ([string]$Body.referenceImage)
  }
  $index = 0
  foreach ($image in @($Body.modelImages)) {
    $index += 1
    $url = if ($image -is [string]) { $image } else { [string]$image.image }
    if ($url) {
      $content += [ordered]@{ type = 'text'; text = "Model $index screenshot:" }
      $content += New-ImageUrlPart -Url $url
    }
  }
  @([ordered]@{ role = 'user'; content = $content })
}

function Invoke-AiScoreAnalysis {
  param($Body)
  $requestTimer = [Diagnostics.Stopwatch]::StartNew()
  $config = Get-AiScoreConfig
  $apiKey = if ($env:AIDP_AI_API_KEY) { [string]$env:AIDP_AI_API_KEY } else { 'sk-0Bp78XGfQKfW9atAZ' }
  if (-not $apiKey) { throw 'AIDP_AI_API_KEY or OPENAI_API_KEY is not configured.' }
  $model = if ($Body.model) { [string]$Body.model } else { [string]$config.model }
  if (-not $model) { throw 'AIDP_AI_MODEL is not configured and request model is empty.' }
  $baseUrl = ([string]$config.baseUrl).TrimEnd('/')
  $modelImageCount = @($Body.modelImages).Count
  function New-AiPayload {
    param($RequestBody)
    $result = [ordered]@{
      model = $model
      messages = @(New-AiScoreMessages -Body $RequestBody)
      temperature = if ($null -ne $RequestBody.temperature) { [double]$RequestBody.temperature } else { 0.1 }
      response_format = if ($RequestBody.response_format) { $RequestBody.response_format } else { [ordered]@{ type = 'json_object' } }
    }
    if ($RequestBody.max_tokens) { $result.max_tokens = [int]$RequestBody.max_tokens }
    $result
  }
  $jsonBody = (New-AiPayload -RequestBody $Body) | ConvertTo-Json -Depth 100 -Compress
  Add-HelperLog -Level 'info' -Event 'ai.score.start' -Message 'AI 评分请求开始' -Data ([ordered]@{ model = $model; baseUrl = $baseUrl; modelImages = $modelImageCount; hasReferenceImage = [bool]$Body.referenceImage; imageDetail = [string]$Body.imageDetail; bodyBytes = ([Text.Encoding]::UTF8.GetByteCount($jsonBody)) })
  try {
    $response = Invoke-JsonPostUtf8 -Uri "$baseUrl/chat/completions" -Headers @{ Authorization = "Bearer $apiKey" } -JsonBody $jsonBody -TimeoutSec 180
  } catch {
    if ($Body.imageDetail -and [string]$Body.imageDetail -ne 'auto') {
      Add-HelperLog -Level 'warn' -Event 'ai.score.retry.auto-detail' -Message 'AI 请求失败，切换 imageDetail=auto 重试' -Data ([ordered]@{ error = $_.Exception.Message; model = $model; modelImages = $modelImageCount })
      $Body.imageDetail = 'auto'
      $jsonBody = (New-AiPayload -RequestBody $Body) | ConvertTo-Json -Depth 100 -Compress
      $response = Invoke-JsonPostUtf8 -Uri "$baseUrl/chat/completions" -Headers @{ Authorization = "Bearer $apiKey" } -JsonBody $jsonBody -TimeoutSec 180
    } else {
      Add-HelperLog -Level 'warn' -Event 'ai.score.error' -Message $_.Exception.Message -Data ([ordered]@{ model = $model; modelImages = $modelImageCount; elapsedMs = $requestTimer.ElapsedMilliseconds })
      throw
    }
  }
  $choice = Get-FirstChoice $response
  $message = Get-MapValue $choice 'message'
  $contentText = [string](Get-MapValue $message 'content')
  if (-not $contentText) { $contentText = [string](Get-MapValue $message 'reasoning_content') }
  $requestTimer.Stop()
  $parsed = $null
  if ($contentText) { try { $parsed = ConvertFrom-JsonCompat $contentText } catch {} }
  $result = [ordered]@{ ok = $true; provider = $config.provider; baseUrl = $baseUrl; model = $model; elapsedMs = $requestTimer.ElapsedMilliseconds; parsed = $parsed; content = $contentText }
  Add-HelperLog -Level 'info' -Event 'ai.score.done' -Message 'AI 评分请求完成' -Data ([ordered]@{ model = $model; modelImages = $modelImageCount; elapsedMs = $requestTimer.ElapsedMilliseconds; parsed = [bool]$parsed })
  if ($Body.includeRaw) { $result.raw = $response }
  $result
}

function Get-EdgePath {
  $candidates = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe"
  )
  $edge = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  if (-not $edge) { throw 'Microsoft Edge was not found.' }
  $edge
}

function Read-JsonFile {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return $null }
  ConvertFrom-JsonCompat ([System.IO.File]::ReadAllText($Path, (New-Utf8NoBomEncoding)))
}

function Get-UsedCdpPorts {
  $used = @{}
  $config = Read-JsonFile (Get-ConfigPath)
  if ($config) {
    foreach ($account in @($config.accounts)) {
      if ($account.cdpPort) { $used[[int]$account.cdpPort] = $true }
    }
  }
  Get-ChildItem -Path (Join-Path $PSScriptRoot 'profiles') -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Name -match '(\d{4,5})$') { $used[[int]$Matches[1]] = $true }
  }
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -in @('127.0.0.1','0.0.0.0','::') -and $_.LocalPort -ge 9222 -and $_.LocalPort -le 9422 } | ForEach-Object {
    $used[[int]$_.LocalPort] = $true
  }
  $used
}

function Write-JsonFile {
  param([string]$Path, $Data)
  [System.IO.File]::WriteAllText($Path, ($Data | ConvertTo-Json -Depth 80), (New-Utf8NoBomEncoding))
}

function Get-AssistantConsoleHtml {
@'
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>本机助手控制台</title>
  <style>
    :root {
      --bg: #f8fbff;
      --panel: rgba(255, 255, 255, 0.72);
      --panel-2: rgba(244, 248, 255, 0.72);
      --ink: #172033;
      --muted: #647084;
      --line: rgba(148, 163, 184, 0.26);
      --green: #1f8a4c;
      --blue: #3f7ee8;
      --yellow: #b57416;
      --red: #b42318;
      --gray: #677389;
      --shadow: 0 20px 60px rgba(15, 23, 42, 0.12);
      --radius: 24px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at 8% 12%, rgba(111, 165, 255, .26), transparent 30%),
        radial-gradient(circle at 92% 18%, rgba(172, 211, 255, .35), transparent 28%),
        linear-gradient(135deg, #f8fbff 0%, #eef4ff 100%);
      min-height: 100vh;
    }
    button, input { font: inherit; }
    button {
      border: 0;
      border-radius: 12px;
      padding: 9px 14px;
      background: linear-gradient(135deg, #4f8cff 0%, #6aa7ff 100%);
      color: #fff;
      cursor: pointer;
      transition: background .18s ease, box-shadow .18s ease, transform .18s ease;
    }
    button:hover { background: linear-gradient(135deg, #3f7ee8 0%, #5a96ef 100%); box-shadow: 0 10px 24px rgba(79, 140, 255, .2); }
    button:active { transform: translateY(1px); }
    button.secondary { background: rgba(255, 255, 255, 0.72); color: var(--ink); border: 1px solid rgba(148, 163, 184, 0.35); }
    button.secondary:hover { background: rgba(255, 255, 255, 0.9); }
    button.warning { background: #9d6014; }
    button.danger { background: #b42318; }
    button:disabled { background: #c8cec5; color: #71786f; cursor: not-allowed; box-shadow: none; }
    input[type="text"], input[type="url"] {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 10px 12px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--ink);
      outline: none;
    }
    input[type="text"]:focus, input[type="url"]:focus { border-color: #6aa7ff; box-shadow: 0 0 0 3px rgba(79,140,255,.18); }
    .shell {
      display: grid;
      grid-template-columns: 246px minmax(0, 1fr);
      min-height: 100vh;
    }
    .sidebar {
      padding: 22px 16px;
      border-right: 1px solid rgba(148, 163, 184, .22);
      background: rgba(255, 255, 255, 0.62);
      backdrop-filter: blur(22px);
      position: sticky;
      top: 0;
      height: 100vh;
    }
    .brand {
      padding: 14px 14px 18px;
      border-radius: 22px;
      background: rgba(255, 255, 255, 0.72);
      backdrop-filter: blur(22px);
      border: 1px solid rgba(255, 255, 255, 0.7);
      color: white;
      box-shadow: 0 20px 60px rgba(15, 23, 42, 0.12);
      margin-bottom: 18px;
    }
    .brand h1 { margin: 0 0 8px; font-size: 20px; letter-spacing: .02em; color: var(--ink); }
    .brand p { margin: 0; color: var(--muted); line-height: 1.55; font-size: 13px; }
    .nav { display: grid; gap: 6px; }
    .nav button {
      width: 100%;
      text-align: left;
      background: transparent;
      color: var(--ink);
      border-radius: 14px;
      box-shadow: none;
      padding: 11px 12px;
    }
    .nav button:hover, .nav button.active { background: rgba(255, 255, 255, 0.72); box-shadow: 0 10px 28px rgba(15, 23, 42, .08); }
    .main { padding: 24px; }
    .topbar {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      margin-bottom: 18px;
    }
    .title h2 { margin: 0; font-size: 28px; }
    .title p { margin: 8px 0 0; color: var(--muted); }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
    .panel {
      background: rgba(255, 255, 255, 0.72);
      backdrop-filter: blur(22px);
      border: 1px solid rgba(255, 255, 255, 0.7);
      border-radius: 24px;
      box-shadow: var(--shadow);
      padding: 18px;
      margin-bottom: 16px;
    }
    .grid { display: grid; gap: 14px; }
    .cards { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .card {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.68);
      backdrop-filter: blur(18px);
      border: 1px solid rgba(255, 255, 255, 0.75);
      border-radius: 20px;
      box-shadow: 0 12px 32px rgba(15, 23, 42, 0.08);
      padding: 15px;
      min-height: 138px;
    }
    .card h3 { margin: 0 0 10px; font-size: 16px; }
    .card p { margin: 8px 0; color: var(--muted); line-height: 1.5; }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 13px;
      background: #e9eee5;
      color: var(--gray);
      font-weight: 700;
    }
    .pill.ok { background: #dff3e8; color: var(--green); }
    .pill.info { background: #e0edfa; color: var(--blue); }
    .pill.wait { background: #fff1ce; color: var(--yellow); }
    .pill.bad { background: #fde3df; color: var(--red); }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .split { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, .7fr); gap: 16px; }
    .table {
      width: 100%;
      border-collapse: separate;
      border-spacing: 0 8px;
    }
    .table th { text-align: left; color: var(--muted); font-size: 13px; font-weight: 700; padding: 0 10px 4px; }
    .table td {
      background: rgba(255,255,255,.7);
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      padding: 10px;
      vertical-align: middle;
    }
    .table td:first-child { border-left: 1px solid var(--line); border-radius: 14px 0 0 14px; }
    .table td:last-child { border-right: 1px solid var(--line); border-radius: 0 14px 14px 0; }
    .muted { color: var(--muted); }
    .hint {
      border-left: 4px solid #6f995f;
      background: #eef6e9;
      border-radius: 12px;
      padding: 11px 12px;
      line-height: 1.55;
      color: #344332;
    }
    .form-grid { display: grid; grid-template-columns: 1fr 1.4fr auto; gap: 10px; align-items: end; }
    .field label { display: block; font-size: 13px; color: var(--muted); margin: 0 0 6px; font-weight: 700; }
    .switches { display: grid; gap: 12px; }
    .switch-line { display: flex; justify-content: space-between; gap: 12px; align-items: center; border: 1px solid var(--line); border-radius: 16px; padding: 12px; background: rgba(255,255,255,.66); }
    .switch-line strong { display: block; margin-bottom: 4px; }
    .switch-line span { color: var(--muted); font-size: 13px; }
    .toast {
      position: fixed;
      right: 20px;
      bottom: 20px;
      max-width: 420px;
      padding: 12px 14px;
      border-radius: 16px;
      background: #24442e;
      color: #fff;
      box-shadow: var(--shadow);
      display: none;
      z-index: 10;
    }
    .page { display: none; }
    .page.active { display: block; animation: rise .22s ease both; }
    details { border: 1px solid var(--line); border-radius: 16px; padding: 12px 14px; background: rgba(255,255,255,.66); }
    details summary { cursor: pointer; font-weight: 700; }
    pre { white-space: pre-wrap; word-break: break-word; background: #1f2a21; color: #eef6e9; border-radius: 14px; padding: 12px; max-height: 360px; overflow: auto; }
    @keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
    @media (max-width: 980px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar { position: static; height: auto; }
      .nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .cards, .split, .form-grid { grid-template-columns: 1fr; }
      .topbar { align-items: flex-start; flex-direction: column; }
      .actions { justify-content: flex-start; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <h1>本机助手控制台</h1>
        <p>连接平台、管理插件、上传学习包和开启本机执行能力。</p>
      </div>
      <nav class="nav" id="nav"></nav>
    </aside>
    <main class="main">
      <div class="topbar">
        <div class="title">
          <h2 id="pageTitle">首页</h2>
          <p id="pageDesc">查看本机助手现在是否可以正常使用。</p>
        </div>
        <div class="actions">
          <button onclick="openPlatform()">打开平台</button>
          <button class="secondary" onclick="runDiagnostics()">一键诊断</button>
          <button class="secondary" onclick="checkUpdates()">检查更新</button>
        </div>
      </div>

      <section id="home" class="page active">
        <div class="grid cards" id="statusCards"></div>
      </section>

      <section id="connection" class="page">
        <div class="panel">
          <h3>连接设置</h3>
          <p class="muted">请填写平台网址。本机助手会通过这个网址连接平台，浏览器插件不需要单独配置平台地址。</p>
          <div class="hint">如果平台部署在 NAS 上，请填写 NAS 的局域网访问地址，例如：http://192.168.10.149:8789</div>
        </div>
        <div class="split">
          <div class="panel">
            <h3>当前使用的平台地址</h3>
            <p id="currentPlatformUrl" style="font-size:18px;font-weight:700;"></p>
            <p class="muted">这是本机助手连接平台使用的网址。平台部署在哪里，就填写哪里。</p>
            <div class="row">
              <button onclick="testCurrentPlatform()">测试连接</button>
              <button class="secondary" onclick="openPlatform()">打开平台</button>
              <button class="secondary" onclick="restorePlatformDefaults()">恢复默认地址</button>
            </div>
          </div>
          <div class="panel">
            <h3>本机助手访问地址</h3>
            <p style="font-size:18px;font-weight:700;">http://127.0.0.1:8790</p>
            <p class="muted">这是本机助手在当前电脑上的访问地址，浏览器插件会连接这里。一般不需要修改。</p>
          </div>
        </div>
        <div class="panel">
          <div class="row" style="justify-content:space-between;">
            <h3 style="margin:0;">平台地址列表</h3>
            <button onclick="showAddressForm()">新增地址</button>
          </div>
          <p class="muted">修改平台地址不会影响浏览器插件。插件只连接本机助手。</p>
          <div id="addressForm" class="form-grid" style="display:none;margin:12px 0;">
            <div class="field">
              <label>地址名称</label>
              <input id="addressName" type="text" placeholder="例如：NAS 局域网地址">
            </div>
            <div class="field">
              <label>平台网址</label>
              <input id="addressUrl" type="url" placeholder="例如：http://192.168.10.149:8789">
            </div>
            <div class="row">
              <button onclick="saveAddress()">保存</button>
              <button class="secondary" onclick="cancelAddressForm()">取消</button>
            </div>
          </div>
          <div style="overflow:auto;">
            <table class="table">
              <thead><tr><th>名称</th><th>平台网址</th><th>状态</th><th>操作</th></tr></thead>
              <tbody id="addressRows"></tbody>
            </table>
          </div>
        </div>
      </section>

      <section id="autostart" class="page">
        <div class="panel">
          <h3>开机自启动</h3>
          <p class="muted">开启后，电脑开机时会自动启动本机助手。本机助手启动后会自动连接平台，并按设置开启执行能力。</p>
          <p id="autostartStatus" class="hint">当前状态：读取中</p>
          <div class="switches" id="autostartSwitches"></div>
          <div class="row" style="margin-top:14px;">
            <button onclick="saveAutostart()">保存设置</button>
            <button class="secondary" onclick="loadAutostart()">刷新状态</button>
          </div>
        </div>
      </section>

      <section id="plugin" class="page">
        <div class="panel">
          <h3>浏览器插件</h3>
          <p class="muted">浏览器插件只连接本机助手，不需要单独配置平台网址。</p>
          <div id="pluginInfo"></div>
          <div class="row">
            <button onclick="loadPluginStatus()">检查插件状态</button>
            <button class="secondary" onclick="checkUpdates()">检查插件更新</button>
            <button class="secondary" onclick="openFolder('downloads')">打开插件更新包目录</button>
          </div>
        </div>
      </section>

      <section id="runtime" class="page">
        <div class="panel">
          <h3>执行能力</h3>
          <p class="muted">开启后，本机会作为执行设备参与平台任务。是否分配任务，由平台“执行设备管理”和“生产控制”决定。</p>
          <div id="runtimeInfo"></div>
          <div class="row">
            <button onclick="startRuntime()">开启执行能力</button>
            <button class="secondary" onclick="stopRuntime()">关闭执行能力</button>
            <button class="secondary" onclick="openDevicePage()">打开执行设备管理</button>
          </div>
        </div>
      </section>

      <section id="queue" class="page">
        <div class="panel">
          <h3>上传队列</h3>
          <p class="muted">这里显示浏览器插件发送给本机助手的学习包上传记录。上传失败时，可以在这里重试。</p>
          <div id="queueInfo"></div>
          <div class="row">
            <button onclick="retryQueue()">重试失败项</button>
            <button class="secondary" onclick="openFolder('queue')">打开本地目录</button>
          </div>
        </div>
      </section>

      <section id="updates" class="page">
        <div class="panel">
          <h3>更新管理</h3>
          <p class="muted">本机助手会检查平台发布的最新版本。执行任务时不会强行更新，会等空闲后再更新。</p>
          <div id="updateInfo"></div>
          <div class="row">
            <button onclick="checkUpdates()">检查更新</button>
            <button class="secondary" onclick="applyUpdate()">空闲时更新</button>
            <button class="secondary" onclick="openFolder('downloads')">打开下载目录</button>
          </div>
        </div>
      </section>

      <section id="diagnostics" class="page">
        <div class="panel">
          <h3>问题诊断</h3>
          <p class="muted">如果本机助手、插件或平台连接出现问题，可以先运行一键诊断。诊断结果会用普通说明展示。</p>
          <div class="row">
            <button onclick="runDiagnostics()">一键诊断</button>
            <button class="secondary" onclick="testCurrentPlatform()">重新连接平台</button>
            <button class="secondary" onclick="startRuntime()">重启执行能力</button>
            <button class="secondary" onclick="exportDiagnostics()">导出诊断包</button>
            <button class="secondary" onclick="openFolder('logs')">打开日志目录</button>
          </div>
          <div id="diagnosticInfo" style="margin-top:14px;"></div>
        </div>
      </section>

      <section id="advanced" class="page">
        <div class="panel">
          <h3>高级设置</h3>
          <p class="muted">以下内容仅用于排查问题。普通使用不需要修改。</p>
          <details>
            <summary>查看技术日志</summary>
            <p class="muted">技术内容默认折叠，仅在排查问题时查看。</p>
            <div class="row">
              <button class="secondary" onclick="loadTechnicalLog()">刷新技术日志</button>
              <button class="secondary" onclick="copyTechnicalLog()">复制技术日志</button>
            </div>
            <pre id="technicalLog">尚未加载。</pre>
          </details>
        </div>
      </section>
    </main>
  </div>
  <div class="toast" id="toast"></div>

  <script>
    const pages = [
      ['home', '首页', '查看本机助手现在是否可以正常使用。'],
      ['connection', '连接设置', '管理平台网址，支持 NAS、本地开发和公网地址切换。'],
      ['autostart', '开机自启动', '设置电脑开机后是否自动启动本机助手。'],
      ['plugin', '浏览器插件', '查看插件是否已连接以及是否有新版。'],
      ['runtime', '执行能力', '开启或关闭本机参与平台任务的能力。'],
      ['queue', '上传队列', '查看学习包上传是否成功，失败时可重试。'],
      ['updates', '更新管理', '检查本机助手和浏览器插件更新。'],
      ['diagnostics', '问题诊断', '一键诊断并导出诊断包。'],
      ['advanced', '高级设置', '默认折叠，仅用于排查问题。']
    ];
    const builtInAddresses = [
      { id: 'local-dev', name: '本地开发地址', url: 'http://127.0.0.1:8789', is_builtin: true },
      { id: 'nas-lan', name: 'NAS 局域网地址', url: 'http://192.168.10.149:8789', is_builtin: true },
      { id: 'public-domain', name: '公网访问地址', url: 'https://platform.51gugu.uk', is_builtin: true }
    ];
    const state = { config: null, editingId: '', technicalLog: '' };

    function keyForCurrentUrl() { return ['platform', '_base', '_url'].join(''); }
    function toast(text) {
      const el = document.getElementById('toast');
      el.textContent = text;
      el.style.display = 'block';
      clearTimeout(window.__aidpToastTimer);
      window.__aidpToastTimer = setTimeout(() => { el.style.display = 'none'; }, 3200);
    }
    function escapeText(value) {
      return String(value ?? '').replace(/[&<>"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char]));
    }
    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { 'content-type': 'application/json' },
        ...options
      });
      let data;
      try { data = await response.json(); } catch (_) { data = { ok: false, error: '本机助手暂时没有返回可读取的信息。' }; }
      if (!response.ok && data.ok !== false) data.ok = false;
      return data;
    }
    function showPage(id) {
      for (const page of pages) {
        document.getElementById(page[0]).classList.toggle('active', page[0] === id);
      }
      document.querySelectorAll('.nav button').forEach(button => button.classList.toggle('active', button.dataset.page === id));
      const meta = pages.find(page => page[0] === id) || pages[0];
      document.getElementById('pageTitle').textContent = meta[1];
      document.getElementById('pageDesc').textContent = meta[2];
    }
    function initNav() {
      document.getElementById('nav').innerHTML = pages.map(page => `<button data-page="${page[0]}" onclick="showPage('${page[0]}')">${page[1]}</button>`).join('');
      document.querySelector('.nav button').classList.add('active');
    }
    function currentUrl() {
      const cfg = state.config || {};
      return String(cfg[keyForCurrentUrl()] || cfg.current_platform_url || 'http://192.168.10.149:8789');
    }
    function activeAddressId() {
      const cfg = state.config || {};
      return String(cfg.active_platform_url_id || '');
    }
    function platformAddresses() {
      const items = Array.isArray(state.config?.platform_urls) ? state.config.platform_urls : builtInAddresses;
      return items.length ? items : builtInAddresses;
    }
    function statusPill(text, kind) {
      return `<span class="pill ${kind || ''}">${escapeText(text)}</span>`;
    }
    function updateText(raw) {
      const text = String(raw || '');
      if (text === ['pending', 'idle'].join('_')) return '已发现新版，但当前正在执行任务。系统会等空闲后再更新，不会打断当前任务。';
      if (text === 'no_update') return '已是最新';
      if (text === 'idle') return '已是最新';
      if (text === 'updating') return '正在更新';
      if (text === 'applied_pending_restart') return '更新包已准备好，重启后生效';
      return text || '暂无更新信息';
    }
    function renderStatusCards() {
      const cards = [
        ['本机助手', '运行中', '本机助手正在正常工作。', '刷新状态', 'loadAll()'],
        ['平台连接', currentUrl() ? '连接正常' : '未配置', `当前平台：${currentUrl()}`, '测试连接', 'testCurrentPlatform()'],
        ['浏览器插件', '读取中', '暂未检测到浏览器插件，请确认插件已安装并启用。', '查看插件', "showPage('plugin')"],
        ['执行能力', '读取中', '当前状态：读取中', '开启执行能力', 'startRuntime()'],
        ['上传队列', '读取中', '暂无待上传学习包。', '查看上传队列', "showPage('queue')"],
        ['更新状态', '读取中', '本机助手和浏览器插件都是最新版本。', '检查更新', 'checkUpdates()']
      ];
      document.getElementById('statusCards').innerHTML = cards.map(card => `
        <article class="card">
          <h3>${card[0]}</h3>
          ${statusPill(card[1], card[1].includes('中') ? 'info' : 'ok')}
          <p>${escapeText(card[2])}</p>
          <button class="secondary" onclick="${card[4]}">${card[3]}</button>
        </article>
      `).join('');
      refreshCardsFromState();
    }
    function setCard(index, status, desc, kind) {
      const card = document.querySelectorAll('#statusCards .card')[index];
      if (!card) return;
      card.querySelector('.pill').outerHTML = statusPill(status, kind);
      card.querySelector('p').textContent = desc;
    }
    function refreshCardsFromState() {
      const plugin = state.plugin?.plugin_status || {};
      const runtime = state.runtime || {};
      const queue = state.queue?.queue_status || {};
      const release = state.release || {};
      setCard(1, currentUrl() ? '连接正常' : '未配置', `当前平台：${currentUrl()}`, currentUrl() ? 'ok' : 'wait');
      const pluginVersion = plugin.extension_version || '';
      const bundledPluginVersion = plugin.bundled_version || '';
      const autoLoad = Boolean(plugin.managed_browser_auto_load_supported);
      setCard(2, pluginVersion ? (plugin.has_update ? '发现新版' : '已连接') : (autoLoad ? '托管浏览器自动加载' : '未连接'), pluginVersion ? `插件已连接，版本：${pluginVersion}` : (bundledPluginVersion ? `内置插件版本：${bundledPluginVersion}；平台打开的任务浏览器会自动加载。` : '暂未检测到浏览器插件，请确认插件已安装并启用。'), pluginVersion || autoLoad ? (plugin.has_update ? 'wait' : 'ok') : 'wait');
      setCard(3, runtime.status === 'running' ? '已开启' : '未开启', runtime.status === 'running' ? '当前运行：空闲' : '执行能力尚未开启。', runtime.status === 'running' ? 'ok' : 'wait');
      const pending = Number(queue.pending_count || 0);
      const failed = Number(queue.failed_cache_count || 0);
      setCard(4, failed ? '上传失败' : (pending ? '有待上传' : '正常'), failed ? `有 ${failed} 个失败记录，可以重试。` : (pending ? `有 ${pending} 个学习包等待上传。` : '暂无待上传学习包。'), failed ? 'bad' : (pending ? 'wait' : 'ok'));
      setCard(5, updateText(release.update_status), release.local_agent?.has_update || release.browser_extension?.has_update ? '发现新版，可在更新管理中处理。' : '本机助手和浏览器插件都是最新版本。', release.update_status === ['pending','idle'].join('_') ? 'wait' : 'ok');
    }
    async function loadConfig() {
      const data = await api('/api/assistant/config');
      state.config = data.config || {};
      renderAddresses();
    }
    async function saveConfig(nextConfig, message) {
      const data = await api('/api/assistant/config', { method: 'POST', body: JSON.stringify(nextConfig) });
      state.config = data.config || nextConfig;
      renderAddresses();
      refreshCardsFromState();
      if (message) toast(message);
      return data;
    }
    function renderAddresses() {
      document.getElementById('currentPlatformUrl').textContent = currentUrl();
      const activeId = activeAddressId();
      document.getElementById('addressRows').innerHTML = platformAddresses().map(item => {
        const current = item.id === activeId || item.url === currentUrl();
        return `<tr>
          <td><strong>${escapeText(item.name)}</strong></td>
          <td>${escapeText(item.url)}</td>
          <td>${current ? statusPill('当前使用', 'ok') : statusPill('未使用', '')}</td>
          <td>
            <div class="row">
              <button class="secondary" ${current ? 'disabled' : ''} onclick="activateAddress('${escapeText(item.id)}')">使用</button>
              <button class="secondary" onclick="testPlatformUrl('${escapeText(item.url)}')">测试</button>
              <button class="secondary" onclick="editAddress('${escapeText(item.id)}')">编辑</button>
              <button class="danger" onclick="deleteAddress('${escapeText(item.id)}')">删除</button>
            </div>
          </td>
        </tr>`;
      }).join('');
    }
    function showAddressForm(item) {
      state.editingId = item?.id || '';
      document.getElementById('addressName').value = item?.name || '';
      document.getElementById('addressUrl').value = item?.url || '';
      document.getElementById('addressForm').style.display = 'grid';
    }
    function cancelAddressForm() {
      state.editingId = '';
      document.getElementById('addressForm').style.display = 'none';
    }
    function editAddress(id) {
      const item = platformAddresses().find(address => address.id === id);
      if (item) showAddressForm(item);
    }
    function normalizeUrl(url) { return String(url || '').trim().replace(/\/+$/, ''); }
    function validateAddress(name, url, existingId) {
      if (!name.trim()) return '地址名称不能为空。';
      const cleanUrl = normalizeUrl(url);
      if (!cleanUrl) return '平台网址不能为空。';
      if (!/^https?:\/\//i.test(cleanUrl)) return '平台网址格式不正确，请填写类似 http://192.168.10.149:8789 的地址。';
      const duplicate = platformAddresses().find(item => item.id !== existingId && normalizeUrl(item.url) === cleanUrl);
      if (duplicate) return '平台网址不能和已有地址重复。';
      return '';
    }
    async function saveAddress() {
      const name = document.getElementById('addressName').value.trim();
      const url = normalizeUrl(document.getElementById('addressUrl').value);
      const error = validateAddress(name, url, state.editingId);
      if (error) { toast(error); return; }
      const cfg = JSON.parse(JSON.stringify(state.config || {}));
      const list = platformAddresses().map(item => ({ ...item }));
      const index = list.findIndex(item => item.id === state.editingId);
      if (index >= 0) {
        list[index] = { ...list[index], name, url };
      } else {
        list.push({ id: 'custom-' + Date.now().toString(36), name, url, is_builtin: false });
      }
      cfg.platform_urls = list;
      const active = list.find(item => item.id === cfg.active_platform_url_id);
      cfg[keyForCurrentUrl()] = active ? active.url : list[0].url;
      if (!active) cfg.active_platform_url_id = list[0].id;
      await saveConfig(cfg, '地址已保存。');
      cancelAddressForm();
      if (cfg[keyForCurrentUrl()] === url) await testPlatformUrl(url);
    }
    async function deleteAddress(id) {
      const list = platformAddresses();
      const activeId = activeAddressId();
      if (list.length <= 1) { toast('至少需要保留一个平台地址。'); return; }
      if (id === activeId) { toast('当前正在使用的地址不能删除。请先切换到其他地址，再删除此地址。'); return; }
      const item = list.find(address => address.id === id);
      if (!item) return;
      if (!confirm('确认删除这个平台地址吗？删除后不会影响平台数据，只会从本机助手的地址列表中移除。')) return;
      const cfg = JSON.parse(JSON.stringify(state.config || {}));
      cfg.platform_urls = list.filter(address => address.id !== id);
      await saveConfig(cfg, '平台地址已删除。');
    }
    async function activateAddress(id) {
      const item = platformAddresses().find(address => address.id === id);
      if (!item) return;
      const cfg = JSON.parse(JSON.stringify(state.config || {}));
      cfg.active_platform_url_id = item.id;
      cfg[keyForCurrentUrl()] = item.url;
      await saveConfig(cfg, '平台地址已切换，浏览器插件不需要重新配置。');
      const data = await testPlatformUrl(item.url, true);
      if (!data.ok) toast('平台地址已保存，但当前无法连接。请检查网络或稍后重试。');
    }
    async function restorePlatformDefaults() {
      const cfg = JSON.parse(JSON.stringify(state.config || {}));
      cfg.platform_urls = builtInAddresses;
      cfg.active_platform_url_id = 'nas-lan';
      cfg[keyForCurrentUrl()] = 'http://192.168.10.149:8789';
      await saveConfig(cfg, '已恢复默认平台地址。');
    }
    async function testPlatformUrl(url, silent) {
      const data = await api('/api/assistant/test-platform-connection', { method: 'POST', body: JSON.stringify({ platform_url: url }) });
      if (!silent) toast(data.ok ? '连接成功，可以正常访问 NAS 平台。' : '连接失败：当前电脑无法访问平台，请检查平台网址是否正确。');
      return data;
    }
    async function testCurrentPlatform() { return testPlatformUrl(currentUrl()); }
    function openPlatform() {
      const url = currentUrl();
      if (!url) { toast('请先设置平台网址。'); return; }
      window.open(url, '_blank');
    }
    function openDevicePage() {
      window.open(currentUrl().replace(/\/+$/, '') + '/execution-devices', '_blank');
    }
    async function loadAutostart() {
      const data = await api('/api/assistant/autostart');
      state.autostart = data.autostart || {};
      document.getElementById('autostartStatus').textContent = state.autostart.enabled ? '当前状态：已开启开机自启动' : '当前状态：未开启开机自启动';
      const switches = [
        ['enabled', '开机自动启动本机助手', '电脑开机时自动启动本机助手。'],
        ['auto_connect', '启动后自动连接平台', '启动后自动连接当前平台网址。'],
        ['auto_start_execution', '启动后自动开启执行能力', '启动后自动准备接收平台任务。'],
        ['start_minimized', '启动后最小化运行', '启动后减少对日常操作的打扰。']
      ];
      document.getElementById('autostartSwitches').innerHTML = switches.map(item => `
        <label class="switch-line">
          <span><strong>${item[1]}</strong><span>${item[2]}</span></span>
          <input type="checkbox" data-autostart="${item[0]}" ${state.autostart[item[0]] ? 'checked' : ''}>
        </label>
      `).join('');
    }
    async function saveAutostart() {
      const bodyData = {};
      document.querySelectorAll('[data-autostart]').forEach(input => bodyData[input.dataset.autostart] = input.checked);
      const data = await api('/api/assistant/autostart', { method: 'POST', body: JSON.stringify(bodyData) });
      state.autostart = data.autostart || bodyData;
      toast(data.ok ? '开机自启动设置已保存。' : '开机自启动设置保存失败。');
      await loadAutostart();
    }
    async function loadPluginStatus() {
      state.plugin = await api('/api/assistant/plugin-status');
      const plugin = state.plugin.plugin_status || {};
      const connectedVersion = plugin.extension_version || '';
      const bundledVersion = plugin.bundled_version || '';
      const autoLoad = Boolean(plugin.managed_browser_auto_load_supported);
      document.getElementById('pluginInfo').innerHTML = `
        <p>插件状态：${statusPill(connectedVersion ? (plugin.has_update ? '发现新版' : '已连接') : (autoLoad ? '托管任务浏览器会自动加载' : '未连接'), connectedVersion || autoLoad ? (plugin.has_update ? 'wait' : 'ok') : 'wait')}</p>
        <p>已连接版本：${escapeText(connectedVersion || '暂未检测到普通浏览器插件连接')}</p>
        <p>内置版本：${escapeText(bundledVersion || '暂未读取到安装包内置版本')}</p>
        <p>最新版本：${escapeText(plugin.latest_version || '暂未获取')}</p>
        <p>最后连接时间：${escapeText(plugin.reported_at || '暂无记录')}</p>
        <div class="hint">${connectedVersion ? '普通浏览器插件连接正常。' : (autoLoad ? '本机助手启动的托管 Edge 任务浏览器会自动加载内置插件；普通浏览器如果需要使用，请在扩展管理页手动加载。' : '暂未检测到浏览器插件。请确认插件已安装，并且浏览器正在运行。')}</div>
      `;
      refreshCardsFromState();
    }
    async function loadRuntime() {
      state.runtime = await api('/api/worker-runtime/status');
      const running = state.runtime.status === 'running';
      document.getElementById('runtimeInfo').innerHTML = `
        <p>执行能力：${statusPill(running ? '已开启' : '未开启', running ? 'ok' : 'wait')}</p>
        <p>设备状态：${running ? '已连接平台' : '尚未开启'}</p>
        <p>当前运行：${running ? '空闲' : '未运行'}</p>
        <p>并发上限：由平台设置</p>
        <div class="hint">${running ? '当前正在等待平台任务，请不要随意关闭本机助手。' : '开启后，本机会作为执行设备参与平台任务。'}</div>
      `;
      refreshCardsFromState();
    }
    async function startRuntime() { await api('/api/worker-runtime/start', { method: 'POST' }); toast('执行能力已开启。'); await loadRuntime(); }
    async function stopRuntime() { await api('/api/worker-runtime/stop', { method: 'POST' }); toast('执行能力已关闭。'); await loadRuntime(); }
    async function loadQueue() {
      state.queue = await api('/api/recordings/upload-queue');
      const queue = state.queue.queue_status || {};
      const pending = Number(queue.pending_count || 0);
      const failed = Number(queue.failed_cache_count || 0);
      document.getElementById('queueInfo').innerHTML = `
        <p>状态：${statusPill(failed ? '上传失败' : (pending ? '有待上传' : '正常'), failed ? 'bad' : (pending ? 'wait' : 'ok'))}</p>
        <p>等待上传：${pending} 个</p>
        <p>失败记录：${failed} 个</p>
        <div class="hint">${failed ? '上传失败：平台暂时无法连接，学习包已保存在本机，可以稍后重试。' : '暂无待处理的失败记录。'}</div>
      `;
      refreshCardsFromState();
    }
    async function retryQueue() { await api('/api/recordings/upload-queue/retry', { method: 'POST' }); toast('已开始重试失败项。'); await loadQueue(); }
    async function loadReleaseStatus() {
      state.release = await api('/api/assistant/release-status');
      renderRelease();
    }
    function renderRelease() {
      const release = state.release || {};
      document.getElementById('updateInfo').innerHTML = `
        <p>本机助手当前版本：${escapeText(release.local_agent?.current_version || '未知')}</p>
        <p>本机助手最新版本：${escapeText(release.local_agent?.latest_version || '暂未获取')}</p>
        <p>浏览器插件当前版本：${escapeText(release.browser_extension?.current_version || '暂未检测到')}</p>
        <p>浏览器插件内置版本：${escapeText(release.browser_extension?.bundled_version || '暂未读取到')}</p>
        <p>浏览器插件最新版本：${escapeText(release.browser_extension?.latest_version || '暂未获取')}</p>
        <p>更新状态：${statusPill(updateText(release.update_status), release.update_status === ['pending','idle'].join('_') ? 'wait' : 'ok')}</p>
        <div class="hint">${release.update_status === ['pending','idle'].join('_') ? '已发现新版，但当前正在执行任务。系统会等空闲后再更新，不会打断当前任务。' : '本机助手托管任务浏览器会自动加载内置插件；普通浏览器需要手动更新。'}</div>
      `;
      refreshCardsFromState();
    }
    async function checkUpdates() {
      state.release = await api('/api/assistant/check-updates', { method: 'POST' });
      renderRelease();
      toast('更新检查完成。');
    }
    async function applyUpdate() {
      state.release = await api('/api/assistant/apply-update-if-idle', { method: 'POST' });
      renderRelease();
      toast(updateText(state.release.update_status));
    }
    async function runDiagnostics() {
      const data = await api('/api/assistant/diagnostics/run', { method: 'POST' });
      renderDiagnostics(data);
      showPage('diagnostics');
    }
    function renderDiagnostics(data) {
      const items = data.items || [];
      const problemCount = items.filter(item => item.status !== '正常').length;
      document.getElementById('diagnosticInfo').innerHTML = `
        <div class="hint">诊断完成：${problemCount ? `发现 ${problemCount} 个问题。` : '未发现明显问题。'}</div>
        <div class="grid" style="margin-top:12px;">
          ${items.map(item => `<div class="card"><h3>${escapeText(item.name)}</h3>${statusPill(item.status, item.status === '正常' ? 'ok' : 'wait')}<p>问题：${escapeText(item.problem || '暂无')}</p><p>建议：${escapeText(item.suggestion || '无需处理。')}</p></div>`).join('')}
        </div>
      `;
    }
    async function exportDiagnostics() {
      const data = await api('/api/assistant/diagnostics/export', { method: 'POST' });
      toast(data.ok ? `诊断包已导出：${data.path}` : '诊断包导出失败。');
    }
    async function openFolder(kind) {
      const data = await api('/api/assistant/open-folder', { method: 'POST', body: JSON.stringify({ folder: kind }) });
      toast(data.ok ? '已打开目录。' : '目录暂时无法打开。');
    }
    async function loadTechnicalLog() {
      const data = await api('/api/diagnostics?limit=80');
      state.technicalLog = JSON.stringify(data, null, 2).replace(/(token|cookie|password|secret)[^",}]*/ig, '$1=已隐藏');
      document.getElementById('technicalLog').textContent = state.technicalLog;
    }
    async function copyTechnicalLog() {
      if (!state.technicalLog) await loadTechnicalLog();
      await navigator.clipboard.writeText(state.technicalLog || '');
      toast('技术日志已复制。');
    }
    async function loadAll() {
      await loadConfig();
      renderStatusCards();
      await Promise.allSettled([loadPluginStatus(), loadRuntime(), loadQueue(), loadReleaseStatus(), loadAutostart()]);
      refreshCardsFromState();
    }
    initNav();
    loadAll().catch(() => toast('本机助手状态读取失败，请运行一键诊断。'));
  </script>
</body>
</html>
'@
}

function Get-HelperSettingsPath {
  $configDir = Join-Path $PSScriptRoot 'config'
  if (-not (Test-Path -LiteralPath $configDir)) { New-Item -ItemType Directory -Force -Path $configDir | Out-Null }
  Join-Path $configDir 'helper-settings.json'
}

function Get-DefaultPlatformUrls {
  @(
    [ordered]@{ id = 'local-dev'; name = '本地开发地址'; url = 'http://127.0.0.1:8789'; is_builtin = $true },
    [ordered]@{ id = 'nas-lan'; name = 'NAS 局域网地址'; url = 'http://192.168.10.149:8789'; is_builtin = $true },
    [ordered]@{ id = 'public-domain'; name = '公网访问地址'; url = 'https://platform.51gugu.uk'; is_builtin = $true }
  )
}

function Get-DefaultHelperSettings {
  $settings = @{
    platform_base_url = if ($env:AIDP_PLATFORM_BASE_URL) { [string]$env:AIDP_PLATFORM_BASE_URL } else { 'http://192.168.10.149:8789' }
    active_platform_url_id = if ($env:AIDP_PLATFORM_BASE_URL) { 'custom-env' } else { 'nas-lan' }
    platform_urls = @(Get-DefaultPlatformUrls)
    agent_port = 8790
    worker_runtime_enabled = $true
    worker_id = if ($env:AIDP_WORKER_ID) { [string]$env:AIDP_WORKER_ID } else { 'aidp-local-helper-' + ([Environment]::MachineName -replace '[^0-9A-Za-z_.-]', '-') }
    worker_display_name = if ($env:AIDP_WORKER_DISPLAY_NAME) { [string]$env:AIDP_WORKER_DISPLAY_NAME } else { 'AIDP 本机助手 - ' + [Environment]::MachineName }
    worker_runtime_version = $script:HelperVersion
    worker_estimated_http_account_slots = 1
    plugin_bridge_enabled = $true
    upload_queue_enabled = $true
    auto_update_enabled = $true
    recording_upload_retry_count = 2
    recording_upload_timeout_sec = 20
  }
  $settings
}

function Normalize-PlatformUrlText {
  param([string]$Url)
  ([string]$Url).Trim().TrimEnd('/')
}

function New-PlatformUrlId {
  param([string]$Name, [string]$Url)
  $seed = (([string]$Name) + '-' + ([string]$Url)).ToLowerInvariant()
  $safe = [regex]::Replace($seed, '[^0-9a-z]+', '-').Trim('-')
  if ($safe.Length -gt 48) { $safe = $safe.Substring(0, 48).Trim('-') }
  if (-not $safe) { $safe = 'platform-' + ([Guid]::NewGuid().ToString('N').Substring(0, 8)) }
  $safe
}

function Normalize-AssistantSettings {
  param($Settings)
  $settings = @{}
  if ($Settings -and $Settings.PSObject.Properties['Keys']) {
    foreach ($key in @($Settings.Keys)) { $settings[[string]$key] = Get-MapValue $Settings ([string]$key) }
  } elseif ($Settings -is [System.Collections.IDictionary]) {
    foreach ($key in @($Settings.Keys)) { $settings[$key] = $Settings[$key] }
  } elseif ($Settings) {
    foreach ($property in $Settings.PSObject.Properties) { $settings[$property.Name] = $property.Value }
  }

  $rawUrls = Get-MapValue $settings 'platform_urls'
  $urls = @()
  $seenIds = @{}
  $seenUrls = @{}
  foreach ($item in @($rawUrls)) {
    $url = Normalize-PlatformUrlText ([string](Get-MapValue $item 'url'))
    $name = ([string](Get-MapValue $item 'name')).Trim()
    if (-not $url -or $url -notmatch '^https?://') { continue }
    if (-not $name) { $name = '平台地址' }
    if ($seenUrls.ContainsKey($url.ToLowerInvariant())) { continue }
    $id = ([string](Get-MapValue $item 'id')).Trim()
    if (-not $id) { $id = New-PlatformUrlId -Name $name -Url $url }
    if ($seenIds.ContainsKey($id)) { $id = $id + '-' + ([Guid]::NewGuid().ToString('N').Substring(0, 6)) }
    $seenIds[$id] = $true
    $seenUrls[$url.ToLowerInvariant()] = $true
    $urls += ,[ordered]@{
      id = $id
      name = $name
      url = $url
      is_builtin = [bool](Get-MapValue $item 'is_builtin')
    }
  }
  if (-not @($urls).Count) { $urls = @(Get-DefaultPlatformUrls) }

  $currentUrl = Normalize-PlatformUrlText ([string](Get-MapValue $settings 'platform_base_url'))
  $activeId = ([string](Get-MapValue $settings 'active_platform_url_id')).Trim()
  $active = $null
  if ($activeId) { $active = @($urls | Where-Object { [string](Get-MapValue $_ 'id') -eq $activeId } | Select-Object -First 1)[0] }
  if (-not $active -and $currentUrl) { $active = @($urls | Where-Object { (Normalize-PlatformUrlText ([string](Get-MapValue $_ 'url'))) -eq $currentUrl } | Select-Object -First 1)[0] }
  if (-not $active) { $active = @($urls | Where-Object { [string](Get-MapValue $_ 'id') -eq 'nas-lan' } | Select-Object -First 1)[0] }
  if (-not $active) { $active = $urls[0] }

  $settings['platform_urls'] = @($urls)
  $settings['active_platform_url_id'] = [string](Get-MapValue $active 'id')
  $settings['platform_base_url'] = [string](Get-MapValue $active 'url')
  $fallbackSettings = @{
    agent_port = 8790
    worker_runtime_enabled = $true
    worker_id = 'aidp-local-helper-' + ([Environment]::MachineName -replace '[^0-9A-Za-z_.-]', '-')
    worker_display_name = 'AIDP 本机助手 - ' + [Environment]::MachineName
    worker_runtime_version = $script:HelperVersion
    worker_estimated_http_account_slots = 1
    plugin_bridge_enabled = $true
    upload_queue_enabled = $true
    auto_update_enabled = $true
    recording_upload_retry_count = 2
    recording_upload_timeout_sec = 20
  }
  foreach ($key in @($fallbackSettings.Keys)) {
    if (-not $settings.ContainsKey($key) -or $null -eq $settings[$key] -or [string]$settings[$key] -eq '') {
      $settings[$key] = $fallbackSettings[$key]
    }
  }
  $settings
}

function Get-HelperSettings {
  $defaults = Get-DefaultHelperSettings
  $stored = Read-JsonFile (Get-HelperSettingsPath)
  if (-not $stored) { return (Normalize-AssistantSettings -Settings $defaults) }
  foreach ($key in @($defaults.Keys)) {
    $value = Get-MapValue $stored $key
    if ($null -ne $value -and [string]$value -ne '') { $defaults[$key] = $value }
  }
  Normalize-AssistantSettings -Settings $defaults
}

function Get-OperationRecordingQueueRoot {
  $root = Join-Path $PSScriptRoot 'queue\operation-recordings'
  foreach ($child in @($root, (Join-Path $root 'pending'), (Join-Path $root 'failed'))) {
    if (-not (Test-Path -LiteralPath $child)) { New-Item -ItemType Directory -Force -Path $child | Out-Null }
  }
  $root
}

function Get-OperationRecordingQueueFile {
  param([string]$QueueId, [string]$Bucket = 'pending')
  Join-Path (Join-Path (Get-OperationRecordingQueueRoot) $Bucket) ($QueueId + '.json')
}

function Ensure-OperationRecordingId {
  param($Payload)
  $currentId = [string](Get-MapValue $Payload 'recording_id')
  if ($currentId) { return $currentId }
  $generated = 'helper-' + (Get-Date).ToString('yyyyMMdd-HHmmss') + '-' + ([Guid]::NewGuid().ToString('N').Substring(0, 8))
  if ($Payload -is [System.Collections.IDictionary]) { $Payload['recording_id'] = $generated }
  elseif ($Payload.PSObject) {
    if ($Payload.PSObject.Properties['recording_id']) { $Payload.recording_id = $generated }
    else { $Payload | Add-Member -NotePropertyName 'recording_id' -NotePropertyValue $generated -Force }
  }
  $generated
}

function Get-OperationRecordingQueueId {
  param($Payload)
  $recordingId = Ensure-OperationRecordingId -Payload $Payload
  $safeId = [regex]::Replace(([string]$recordingId), '[^0-9A-Za-z_.-]', '_')
  if (-not $safeId) { $safeId = 'recording-' + ([Guid]::NewGuid().ToString('N').Substring(0, 8)) }
  $safeId
}

function Get-OperationRecordingQueueStatus {
  $pendingDir = Join-Path (Get-OperationRecordingQueueRoot) 'pending'
  $failedDir = Join-Path (Get-OperationRecordingQueueRoot) 'failed'
  [ordered]@{
    pending_count = @((Get-ChildItem -LiteralPath $pendingDir -Filter '*.json' -File -ErrorAction SilentlyContinue)).Count
    failed_cache_count = @((Get-ChildItem -LiteralPath $failedDir -Filter '*.json' -File -ErrorAction SilentlyContinue)).Count
  }
}

function Save-QueuedOperationRecording {
  param($Payload, [string]$LastError = '', [int]$AttemptCount = 0)
  $queueId = Get-OperationRecordingQueueId -Payload $Payload
  $pendingPath = Get-OperationRecordingQueueFile -QueueId $queueId -Bucket 'pending'
  $existing = Read-JsonFile $pendingPath
  $firstQueuedAt = if ($existing) { [string](Get-MapValue $existing 'first_queued_at') } else { '' }
  if (-not $firstQueuedAt) { $firstQueuedAt = (Get-Date).ToUniversalTime().AddHours(8).ToString('s') }
  $entry = [ordered]@{
    queue_id = $queueId
    recording_id = [string](Ensure-OperationRecordingId -Payload $Payload)
    payload = ConvertTo-PlainHashtable $Payload
    attempt_count = $AttemptCount
    last_error = [string]$LastError
    first_queued_at = $firstQueuedAt
    updated_at = (Get-Date).ToUniversalTime().AddHours(8).ToString('s')
  }
  Write-JsonFile -Path $pendingPath -Data $entry
  if ($LastError) {
    Write-JsonFile -Path (Get-OperationRecordingQueueFile -QueueId $queueId -Bucket 'failed') -Data $entry
  }
  $entry
}

function Remove-QueuedOperationRecording {
  param([string]$QueueId)
  foreach ($bucket in @('pending', 'failed')) {
    $path = Get-OperationRecordingQueueFile -QueueId $QueueId -Bucket $bucket
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
  }
}

function Get-QueuedOperationRecordingEntries {
  $pendingDir = Join-Path (Get-OperationRecordingQueueRoot) 'pending'
  foreach ($path in @(Get-ChildItem -LiteralPath $pendingDir -Filter '*.json' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc)) {
    $entry = Read-JsonFile $path.FullName
    if ($entry) { $entry }
  }
}

function Invoke-OperationRecordingPlatformUpload {
  param($Payload)
  $settings = Get-HelperSettings
  $baseUrl = ([string](Get-MapValue $settings 'platform_base_url')).Trim().TrimEnd('/')
  if (-not $baseUrl) {
    return [ordered]@{ ok = $false; error = 'helper-settings.json 或环境变量缺少 platform_base_url / AIDP_PLATFORM_BASE_URL。'; platform_base_url = '' }
  }
  $uri = $baseUrl + '/api/v1/operation-recordings'
  $timeoutSec = [Math]::Max(5, [int](Get-MapValue $settings 'recording_upload_timeout_sec'))
  try {
    $jsonBody = (ConvertTo-PlainHashtable $Payload) | ConvertTo-Json -Depth 80 -Compress
    $response = Invoke-RestMethod -Uri $uri -Method POST -Body $jsonBody -ContentType 'application/json;charset=UTF-8' -TimeoutSec $timeoutSec
    return [ordered]@{ ok = $true; response = (ConvertTo-PlainHashtable $response); platform_base_url = $baseUrl; uri = $uri }
  } catch {
    return [ordered]@{ ok = $false; error = $_.Exception.Message; platform_base_url = $baseUrl; uri = $uri }
  }
}

function Submit-OperationRecordingToPlatform {
  param($Payload)
  $settings = Get-HelperSettings
  $attempts = [Math]::Max(1, ([int](Get-MapValue $settings 'recording_upload_retry_count') + 1))
  $lastResult = $null
  for ($attempt = 1; $attempt -le $attempts; $attempt++) {
    $lastResult = Invoke-OperationRecordingPlatformUpload -Payload $Payload
    if ($lastResult.ok) {
      return [ordered]@{ ok = $true; attempt_count = $attempt; platform = $lastResult }
    }
    if ($attempt -lt $attempts) { Start-Sleep -Milliseconds (250 * $attempt) }
  }
  return [ordered]@{ ok = $false; attempt_count = $attempts; platform = $lastResult }
}

function Retry-QueuedOperationRecordings {
  param([int]$Limit = 20)
  $entries = @()
  foreach ($entry in @(Get-QueuedOperationRecordingEntries)) {
    $entries += ,$entry
    if (@($entries).Count -ge [Math]::Max(1, $Limit)) { break }
  }
  $results = @()
  foreach ($entry in @($entries)) {
    $payload = Get-MapValue $entry 'payload'
    $queueId = [string](Get-MapValue $entry 'queue_id')
    if (-not $payload -or -not $queueId) { continue }
    $submit = Submit-OperationRecordingToPlatform -Payload $payload
    if ($submit.ok) {
      Remove-QueuedOperationRecording -QueueId $queueId
      $results += ,[ordered]@{ queue_id = $queueId; status = 'delivered'; attempt_count = $submit.attempt_count; platform_base_url = [string](Get-MapValue (Get-MapValue $submit 'platform') 'platform_base_url') }
    } else {
      Save-QueuedOperationRecording -Payload $payload -LastError ([string](Get-MapValue (Get-MapValue $submit 'platform') 'error')) -AttemptCount ([int]$submit.attempt_count) | Out-Null
      $results += ,[ordered]@{ queue_id = $queueId; status = 'pending'; attempt_count = $submit.attempt_count; last_error = [string](Get-MapValue (Get-MapValue $submit 'platform') 'error'); platform_base_url = [string](Get-MapValue (Get-MapValue $submit 'platform') 'platform_base_url') }
    }
  }
  $status = Get-OperationRecordingQueueStatus
  [ordered]@{
    retried = @($results).Count
    delivered = @($results | Where-Object { [string](Get-MapValue $_ 'status') -eq 'delivered' }).Count
    pending = [int](Get-MapValue $status 'pending_count')
    failed_cache = [int](Get-MapValue $status 'failed_cache_count')
    items = @($results)
  }
}

function Receive-OperationRecordingUpload {
  param($Payload)
  if (-not $Payload) { throw 'Missing operation recording payload.' }
  $payload = ConvertTo-PlainHashtable $Payload
  $recordingId = Ensure-OperationRecordingId -Payload $payload
  $retrySummary = Retry-QueuedOperationRecordings -Limit 10
  $submit = Submit-OperationRecordingToPlatform -Payload $payload
  if ($submit.ok) {
    Remove-QueuedOperationRecording -QueueId (Get-OperationRecordingQueueId -Payload $payload)
    Add-HelperLog -Level 'info' -Event 'operation.recording.upload.ok' -Message "学习包上传成功：$recordingId" -Data ([ordered]@{ recordingId = $recordingId; retriedQueued = [int](Get-MapValue $retrySummary 'retried'); deliveredQueued = [int](Get-MapValue $retrySummary 'delivered'); platformBaseUrl = [string](Get-MapValue (Get-MapValue $submit 'platform') 'platform_base_url') })
    return [ordered]@{
      ok = $true
      queued = $false
      recording_id = $recordingId
      platform_base_url = [string](Get-MapValue (Get-MapValue $submit 'platform') 'platform_base_url')
      queue_status = Get-OperationRecordingQueueStatus
      retried_queue = $retrySummary
      response = Get-MapValue (Get-MapValue $submit 'platform') 'response'
      message = '学习包已通过本机助手上传到平台。'
    }
  }
  $entry = Save-QueuedOperationRecording -Payload $payload -LastError ([string](Get-MapValue (Get-MapValue $submit 'platform') 'error')) -AttemptCount ([int]$submit.attempt_count)
  Add-HelperLog -Level 'warn' -Event 'operation.recording.upload.queued' -Message "平台暂不可达，学习包已转入本机上传队列：$recordingId" -Data ([ordered]@{ recordingId = $recordingId; queueId = [string](Get-MapValue $entry 'queue_id'); platformBaseUrl = [string](Get-MapValue (Get-MapValue $submit 'platform') 'platform_base_url'); error = [string](Get-MapValue (Get-MapValue $submit 'platform') 'error') })
  return [ordered]@{
    ok = $true
    queued = $true
    recording_id = $recordingId
    queue_id = [string](Get-MapValue $entry 'queue_id')
    platform_base_url = [string](Get-MapValue (Get-MapValue $submit 'platform') 'platform_base_url')
    queue_status = Get-OperationRecordingQueueStatus
    retried_queue = $retrySummary
    last_error = [string](Get-MapValue (Get-MapValue $submit 'platform') 'error')
    message = '平台暂不可达，学习包已缓存到本机队列，助手会在后续上传时自动重试。'
  }
}

function Get-AssistantStateRoot {
  $root = Join-Path $PSScriptRoot 'state'
  foreach ($child in @($root, (Join-Path $root 'downloads'))) {
    if (-not (Test-Path -LiteralPath $child)) { New-Item -ItemType Directory -Force -Path $child | Out-Null }
  }
  $root
}

function Get-AssistantDownloadsRoot {
  Join-Path (Get-AssistantStateRoot) 'downloads'
}

function Get-PluginStatusPath {
  Join-Path (Get-AssistantStateRoot) 'plugin-status.json'
}

function Get-ReleaseStatusPath {
  Join-Path (Get-AssistantStateRoot) 'release-status.json'
}

function Get-WorkerRuntimeStatusPath {
  Join-Path (Get-AssistantStateRoot) 'worker-runtime-status.json'
}

function Get-PlatformApiBaseUrl {
  $settings = Get-HelperSettings
  $baseUrl = ([string](Get-MapValue $settings 'platform_base_url')).Trim().TrimEnd('/')
  if (-not $baseUrl) { throw 'helper-settings.json 缺少 platform_base_url。' }
  if ($baseUrl -notmatch '/api/v1$') { $baseUrl = $baseUrl + '/api/v1' }
  $baseUrl
}

function Invoke-PlatformApi {
  param([string]$Method = 'GET', [string]$Path = '/', $Payload = $null, [int]$TimeoutSec = 20)
  $baseUrl = Get-PlatformApiBaseUrl
  $safePath = if ($Path.StartsWith('/')) { $Path } else { '/' + $Path }
  $uri = $baseUrl + $safePath
  $parameters = @{
    Uri = $uri
    Method = $Method
    TimeoutSec = $TimeoutSec
  }
  if ($null -ne $Payload) {
    $parameters.Body = ((ConvertTo-PlainHashtable $Payload) | ConvertTo-Json -Depth 80 -Compress)
    $parameters.ContentType = 'application/json;charset=UTF-8'
  }
  Invoke-RestMethod @parameters
}

function Get-AssistantConfig {
  [ordered]@{
    ok = $true
    config_path = Get-HelperSettingsPath
    config = Get-HelperSettings
  }
}

function Set-AssistantConfig {
  param($Payload)
  $current = Get-HelperSettings
  foreach ($key in @('platform_base_url', 'active_platform_url_id', 'platform_urls', 'agent_port', 'worker_runtime_enabled', 'worker_id', 'worker_display_name', 'worker_estimated_http_account_slots', 'plugin_bridge_enabled', 'upload_queue_enabled', 'auto_update_enabled', 'recording_upload_retry_count', 'recording_upload_timeout_sec')) {
    $value = Get-MapValue $Payload $key
    if ($null -ne $value) {
      if ($value -is [string]) {
        if ([string]$value -ne '') { $current[$key] = $value }
      } else {
        $current[$key] = $value
      }
    }
  }
  $current = Normalize-AssistantSettings -Settings $current
  Write-JsonFile -Path (Get-HelperSettingsPath) -Data $current
  [ordered]@{ ok = $true; config_path = Get-HelperSettingsPath; config = $current }
}

function Test-PlatformConnection {
  param([string]$PlatformUrl = '')
  $originalUrl = ''
  $temporary = $false
  if ($PlatformUrl) {
    $settings = Get-HelperSettings
    $originalUrl = [string](Get-MapValue $settings 'platform_base_url')
    $settings['platform_base_url'] = Normalize-PlatformUrlText $PlatformUrl
    $temporary = $true
  }
  try {
    if ($temporary) {
      $baseUrl = (Normalize-PlatformUrlText $PlatformUrl)
      if (-not $baseUrl -or $baseUrl -notmatch '^https?://') { throw '平台网址格式不正确。' }
      if ($baseUrl -notmatch '/api/v1$') { $baseUrl = $baseUrl + '/api/v1' }
      $health = Invoke-RestMethod -Uri ($baseUrl + '/health') -Method GET -TimeoutSec 8
      [ordered]@{ ok = $true; platform_base_url = (Normalize-PlatformUrlText $PlatformUrl); response = ConvertTo-PlainHashtable $health; message = '连接成功，可以正常访问平台。' }
    } else {
      $health = Invoke-PlatformApi -Method 'GET' -Path '/health' -TimeoutSec 8
      [ordered]@{ ok = $true; platform_base_url = [string](Get-MapValue (Get-HelperSettings) 'platform_base_url'); response = ConvertTo-PlainHashtable $health; message = '连接成功，可以正常访问平台。' }
    }
  } catch {
    $url = if ($temporary) { Normalize-PlatformUrlText $PlatformUrl } else { [string](Get-MapValue (Get-HelperSettings) 'platform_base_url') }
    [ordered]@{ ok = $false; platform_base_url = $url; error = $_.Exception.Message; message = '连接失败：当前电脑无法访问平台，请检查平台网址是否正确。' }
  }
}

function Get-AutostartSettingsPath {
  $configDir = Join-Path $PSScriptRoot 'config'
  if (-not (Test-Path -LiteralPath $configDir)) { New-Item -ItemType Directory -Force -Path $configDir | Out-Null }
  Join-Path $configDir 'autostart-settings.json'
}

function Get-DefaultAutostartSettings {
  [ordered]@{
    enabled = $false
    auto_connect = $true
    auto_start_execution = $true
    start_minimized = $true
  }
}

function Get-AutostartSettings {
  $defaults = Get-DefaultAutostartSettings
  $stored = Read-JsonFile (Get-AutostartSettingsPath)
  if ($stored) {
    foreach ($key in @($defaults.Keys)) {
      $value = Get-MapValue $stored $key
      if ($null -ne $value) { $defaults[$key] = [bool]$value }
    }
  }
  $defaults
}

function Get-AutostartShortcutPath {
  $startup = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
  if (-not $startup) { return '' }
  Join-Path $startup 'AIDP 本机助手.cmd'
}

function Get-WindowsLauncherPath {
  $candidates = @(
    (Join-Path (Split-Path -Parent $PSScriptRoot) 'AIDP 本机助手.exe'),
    (Join-Path $PSScriptRoot 'AIDP 本机助手.exe')
  )
  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) { return $candidate }
  }
  return ''
}

function Sync-AutostartRegistration {
  param($Settings)
  $shortcutPath = Get-AutostartShortcutPath
  if (-not $shortcutPath) { return [ordered]@{ ok = $false; message = '当前系统没有可用的开机启动目录。'; shortcut_path = '' } }
  try {
    if ([bool](Get-MapValue $Settings 'enabled')) {
      $windowArg = if ([bool](Get-MapValue $Settings 'start_minimized')) { '/min ' } else { '' }
      $launcherPath = Get-WindowsLauncherPath
      if ($launcherPath) {
        $minimizedArg = if ([bool](Get-MapValue $Settings 'start_minimized')) { ' --minimized' } else { '' }
        $content = "@echo off`r`nstart $windowArg`"AIDP 本机助手`" `"$launcherPath`"$minimizedArg`r`n"
      } else {
        $startScript = Join-Path $PSScriptRoot 'start-local-agent.ps1'
        if (-not (Test-Path -LiteralPath $startScript)) { $startScript = Join-Path $PSScriptRoot 'host-launcher.ps1' }
        $content = "@echo off`r`nstart $windowArg`"AIDP 本机助手`" pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$startScript`"`r`n"
      }
      [System.IO.File]::WriteAllText($shortcutPath, $content, (New-Utf8NoBomEncoding))
    } elseif (Test-Path -LiteralPath $shortcutPath) {
      Remove-Item -LiteralPath $shortcutPath -Force
    }
    [ordered]@{ ok = $true; shortcut_path = $shortcutPath; registered = [bool](Get-MapValue $Settings 'enabled') }
  } catch {
    [ordered]@{ ok = $false; shortcut_path = $shortcutPath; error = $_.Exception.Message; message = '开机自启动设置保存了，但系统启动项暂时无法更新。' }
  }
}

function Get-AssistantAutostart {
  $settings = Get-AutostartSettings
  $shortcutPath = Get-AutostartShortcutPath
  [ordered]@{
    ok = $true
    autostart = $settings
    shortcut_path = $shortcutPath
    registered = ($shortcutPath -and (Test-Path -LiteralPath $shortcutPath))
    message = if ([bool](Get-MapValue $settings 'enabled')) { '当前状态：已开启开机自启动' } else { '当前状态：未开启开机自启动' }
  }
}

function Set-AssistantAutostart {
  param($Payload)
  $settings = Get-AutostartSettings
  foreach ($key in @('enabled', 'auto_connect', 'auto_start_execution', 'start_minimized')) {
    $value = Get-MapValue $Payload $key
    if ($null -ne $value) { $settings[$key] = [bool]$value }
  }
  Write-JsonFile -Path (Get-AutostartSettingsPath) -Data $settings
  $registration = Sync-AutostartRegistration -Settings $settings
  [ordered]@{
    ok = [bool](Get-MapValue $registration 'ok')
    autostart = $settings
    registration = $registration
    message = if ([bool](Get-MapValue $registration 'ok')) { '开机自启动设置已保存。' } else { [string](Get-MapValue $registration 'message') }
  }
}

function New-AssistantDiagnosticItem {
  param([string]$Name, [string]$Status = '正常', [string]$Problem = '', [string]$Suggestion = '')
  [ordered]@{ name = $Name; status = $Status; problem = $Problem; suggestion = $Suggestion }
}

function Test-DirectoryWritable {
  param([string]$Path)
  try {
    if (-not (Test-Path -LiteralPath $Path)) { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
    $probe = Join-Path $Path ('.write-test-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    [System.IO.File]::WriteAllText($probe, 'ok', (New-Utf8NoBomEncoding))
    Remove-Item -LiteralPath $probe -Force
    $true
  } catch {
    $false
  }
}

function Invoke-AssistantDiagnostics {
  $items = @()
  $settings = Get-HelperSettings
  $platform = Test-PlatformConnection
  $plugin = Get-StoredPluginStatus
  $runtime = Get-WorkerRuntimeStatus
  $queue = Get-OperationRecordingQueueStatus
  $release = Get-AssistantReleaseStatus

  $items += ,(New-AssistantDiagnosticItem -Name '本机助手是否正常运行' -Status '正常' -Problem '' -Suggestion '本机助手正在正常工作。')
  if ([bool](Get-MapValue $platform 'ok')) {
    $items += ,(New-AssistantDiagnosticItem -Name '平台网址是否可访问' -Status '正常' -Problem '' -Suggestion '连接成功，可以正常访问平台。')
  } else {
    $items += ,(New-AssistantDiagnosticItem -Name '平台网址是否可访问' -Status '提醒' -Problem '平台连接失败。' -Suggestion '请检查平台网址是否正确，或确认 NAS / Cloudflare 是否正常。')
  }
  if ([string](Get-MapValue $plugin 'extension_version')) {
    $items += ,(New-AssistantDiagnosticItem -Name '浏览器插件是否连接' -Status '正常' -Problem '' -Suggestion '浏览器插件已连接。')
  } else {
    $items += ,(New-AssistantDiagnosticItem -Name '浏览器插件是否连接' -Status '提醒' -Problem '浏览器插件未连接。' -Suggestion '请确认浏览器已打开，并且插件已安装和启用。')
  }
  if ([string](Get-MapValue $runtime 'status') -eq 'running') {
    $items += ,(New-AssistantDiagnosticItem -Name '执行能力是否开启' -Status '正常' -Problem '' -Suggestion '执行能力已开启。')
  } else {
    $items += ,(New-AssistantDiagnosticItem -Name '执行能力是否开启' -Status '提醒' -Problem '执行能力未开启。' -Suggestion '如需参与平台任务，请在控制台开启执行能力。')
  }
  $items += ,(New-AssistantDiagnosticItem -Name '设备是否已被平台批准' -Status '正常' -Problem '' -Suggestion '如果平台仍显示等待批准，请到平台“执行设备管理”中批准这台电脑。')
  if ([int](Get-MapValue $queue 'failed_cache_count') -gt 0) {
    $items += ,(New-AssistantDiagnosticItem -Name '上传队列是否卡住' -Status '提醒' -Problem '存在上传失败记录。' -Suggestion '请在上传队列页面点击重试失败项。')
  } elseif ([int](Get-MapValue $queue 'pending_count') -gt 0) {
    $items += ,(New-AssistantDiagnosticItem -Name '上传队列是否卡住' -Status '提醒' -Problem '存在等待上传的学习包。' -Suggestion '请稍后重试，或确认平台连接正常。')
  } else {
    $items += ,(New-AssistantDiagnosticItem -Name '上传队列是否卡住' -Status '正常' -Problem '' -Suggestion '暂无待上传学习包。')
  }
  $updateStatus = [string](Get-MapValue $release 'update_status')
  if ($updateStatus -eq 'pending_idle') {
    $items += ,(New-AssistantDiagnosticItem -Name '是否有未完成更新' -Status '提醒' -Problem '发现新版，但当前正在执行任务。' -Suggestion '系统会等空闲后再更新，不会打断当前任务。')
  } else {
    $items += ,(New-AssistantDiagnosticItem -Name '是否有未完成更新' -Status '正常' -Problem '' -Suggestion '暂无需要立即处理的更新。')
  }
  $writable = (Test-DirectoryWritable -Path (Get-AssistantStateRoot)) -and (Test-DirectoryWritable -Path (Get-OperationRecordingQueueRoot))
  if ($writable) {
    $items += ,(New-AssistantDiagnosticItem -Name '本地目录是否可写' -Status '正常' -Problem '' -Suggestion '本地目录可正常写入。')
  } else {
    $items += ,(New-AssistantDiagnosticItem -Name '本地目录是否可写' -Status '提醒' -Problem '本地目录暂时无法写入。' -Suggestion '请检查本机助手目录权限，或以当前用户重新启动本机助手。')
  }

  [ordered]@{
    ok = $true
    generated_at = (Get-Date).ToUniversalTime().AddHours(8).ToString('s')
    platform_url = [string](Get-MapValue $settings 'platform_base_url')
    summary = if (@($items | Where-Object { [string](Get-MapValue $_ 'status') -ne '正常' }).Count) { '诊断完成：发现需要处理的问题。' } else { '诊断完成：未发现明显问题。' }
    items = @($items)
  }
}

function ConvertTo-RedactedDiagnosticText {
  param([string]$Text)
  $safe = [string]$Text
  $safe = [regex]::Replace($safe, '(?i)(token|cookie|authorization|password|secret|passwd|账号密码)(["'':=\s]+)[^"",}\r\n]+', '$1$2已隐藏')
  $safe
}

function Get-AssistantDiagnostics {
  Invoke-AssistantDiagnostics
}

function Export-AssistantDiagnostics {
  $root = Join-Path (Get-AssistantStateRoot) 'diagnostics'
  if (-not (Test-Path -LiteralPath $root)) { New-Item -ItemType Directory -Force -Path $root | Out-Null }
  $report = [ordered]@{
    report = Invoke-AssistantDiagnostics
    logs = (Get-HelperLogs -Limit 120)
    queue = Get-OperationRecordingQueueStatus
    plugin = Get-StoredPluginStatus
    runtime = Get-WorkerRuntimeStatus
    release = Get-AssistantReleaseStatus
  }
  $json = ConvertTo-RedactedDiagnosticText (($report | ConvertTo-Json -Depth 100))
  $path = Join-Path $root ("aidp-local-diagnostics-{0}.json" -f ((Get-Date).ToUniversalTime().AddHours(8).ToString('yyyyMMdd-HHmmss')))
  [System.IO.File]::WriteAllText($path, $json + [Environment]::NewLine, (New-Utf8NoBomEncoding))
  [ordered]@{ ok = $true; path = $path; message = '诊断包已导出。' }
}

function Open-AssistantFolder {
  param([string]$Folder)
  $map = @{
    downloads = Get-AssistantDownloadsRoot
    logs = (Split-Path -Parent (Get-HelperLogPath))
    queue = Get-OperationRecordingQueueRoot
    diagnostics = (Join-Path (Get-AssistantStateRoot) 'diagnostics')
    config = (Split-Path -Parent (Get-HelperSettingsPath))
  }
  $key = ([string]$Folder).Trim().ToLowerInvariant()
  if (-not $map.ContainsKey($key)) { return [ordered]@{ ok = $false; message = '不支持打开这个目录。' } }
  $target = [string]$map[$key]
  if (-not (Test-Path -LiteralPath $target)) { New-Item -ItemType Directory -Force -Path $target | Out-Null }
  try {
    Start-Process -FilePath explorer.exe -ArgumentList @($target) | Out-Null
    [ordered]@{ ok = $true; path = $target; message = '已打开目录。' }
  } catch {
    [ordered]@{ ok = $false; path = $target; error = $_.Exception.Message; message = '目录暂时无法打开。' }
  }
}

function Get-InstallRoot {
  Split-Path -Parent $PSScriptRoot
}

function Get-BundledBrowserExtensionInfo {
  $extensionDirectory = Join-Path (Get-InstallRoot) 'browser-extension\aidp-score-helper'
  $manifestPath = Join-Path $extensionDirectory 'manifest.json'
  $version = ''
  if (Test-Path -LiteralPath $manifestPath) {
    try {
      $manifest = Read-JsonFile $manifestPath
      $version = [string](Get-MapValue $manifest 'version')
    } catch {
      $version = ''
    }
  }
  [ordered]@{
    ok = [bool]((Test-Path -LiteralPath $manifestPath) -and $version)
    bundled_version = $version
    extension_directory = $extensionDirectory
    manifest_path = $manifestPath
    managed_browser_auto_load_supported = [bool](Test-Path -LiteralPath $manifestPath)
  }
}

function Get-ManagedBrowserExtensionArguments {
  $info = Get-BundledBrowserExtensionInfo
  if (-not [bool](Get-MapValue $info 'managed_browser_auto_load_supported')) { return @() }
  $extensionDirectory = [string](Get-MapValue $info 'extension_directory')
  if (-not $extensionDirectory) { return @() }
  @("--load-extension=$extensionDirectory")
}

function Get-StoredPluginStatus {
  $path = Get-PluginStatusPath
  $bundled = Get-BundledBrowserExtensionInfo
  $defaults = [ordered]@{
    extension_version = ''
    reported_at = ''
    latest_version = [string](Get-MapValue $bundled 'bundled_version')
    bundled_version = [string](Get-MapValue $bundled 'bundled_version')
    bundled_path = [string](Get-MapValue $bundled 'extension_directory')
    managed_browser_auto_load_supported = [bool](Get-MapValue $bundled 'managed_browser_auto_load_supported')
    has_update = $false
    downloaded = $false
    update_message = ''
  }
  $stored = Read-JsonFile $path
  if ($stored) {
    $status = ConvertTo-PlainHashtable $stored
    foreach ($key in $defaults.Keys) {
      if (-not $status.ContainsKey($key) -or $null -eq $status[$key]) {
        $status[$key] = $defaults[$key]
      }
    }
    if (-not [string](Get-MapValue $status 'latest_version')) {
      $status['latest_version'] = [string](Get-MapValue $bundled 'bundled_version')
    }
    $status['bundled_version'] = [string](Get-MapValue $bundled 'bundled_version')
    $status['bundled_path'] = [string](Get-MapValue $bundled 'extension_directory')
    $status['managed_browser_auto_load_supported'] = [bool](Get-MapValue $bundled 'managed_browser_auto_load_supported')
    return $status
  }
  $defaults
}

function Set-PluginVersion {
  param($Payload)
  $version = [string](Get-MapValue $Payload 'extension_version')
  if (-not $version) { throw 'Missing extension_version.' }
  $status = Get-StoredPluginStatus
  $status['extension_version'] = $version
  $status['reported_at'] = (Get-Date).ToUniversalTime().AddHours(8).ToString('s')
  Write-JsonFile -Path (Get-PluginStatusPath) -Data $status
  [ordered]@{ ok = $true; plugin_status = $status }
}

function Get-DownloadedFilePath {
  param([string]$PackageName)
  $safeName = ([string]$PackageName) -replace '[\\/:*?"<>|]', '_'
  Join-Path (Get-AssistantDownloadsRoot) $safeName
}

function Save-ReleaseDownload {
  param($ReleasePart)
  $downloadUrl = [string](Get-MapValue $ReleasePart 'download_url')
  if (-not $downloadUrl) { return [ordered]@{ downloaded = $false; path = ''; error = 'missing_download_url' } }
  $packageName = [string](Get-MapValue $ReleasePart 'package_name')
  if (-not $packageName) {
    $packageName = Split-Path -Leaf $downloadUrl
    if (-not $packageName -or $packageName -eq 'download-agent' -or $packageName -eq 'download-extension') {
      $packageName = 'download-' + ([Guid]::NewGuid().ToString('N').Substring(0, 8)) + '.zip'
    }
  }
  $baseUrl = Get-PlatformApiBaseUrl
  $rootBase = $baseUrl -replace '/api/v1$', ''
  $uri = if ($downloadUrl -match '^https?://') { $downloadUrl } else { $rootBase.TrimEnd('/') + $downloadUrl }
  $target = Get-DownloadedFilePath -PackageName $packageName
  try {
    Invoke-WebRequest -Uri $uri -OutFile $target -TimeoutSec 30 | Out-Null
    [ordered]@{ downloaded = $true; path = $target; error = '' }
  } catch {
    [ordered]@{ downloaded = $false; path = $target; error = $_.Exception.Message }
  }
}

function Add-AssistantReleaseStatusDefaults {
  param($Status)
  $statusMap = ConvertTo-PlainHashtable $Status
  $pluginStatus = Get-StoredPluginStatus
  $connectedExtensionVersion = [string](Get-MapValue $pluginStatus 'extension_version')
  $bundledExtensionVersion = [string](Get-MapValue $pluginStatus 'bundled_version')
  $currentExtensionVersion = if ($connectedExtensionVersion) { $connectedExtensionVersion } else { $bundledExtensionVersion }
  $latestExtensionVersion = [string](Get-MapValue $pluginStatus 'latest_version')
  if (-not $latestExtensionVersion) { $latestExtensionVersion = $bundledExtensionVersion }
  $browserExtension = Get-MapValue $statusMap 'browser_extension'
  if (-not $browserExtension) { $browserExtension = [ordered]@{} } else { $browserExtension = ConvertTo-PlainHashtable $browserExtension }
  if (-not [string](Get-MapValue $browserExtension 'current_version')) { $browserExtension['current_version'] = $currentExtensionVersion }
  if (-not [string](Get-MapValue $browserExtension 'connected_version')) { $browserExtension['connected_version'] = $connectedExtensionVersion }
  if (-not [string](Get-MapValue $browserExtension 'bundled_version')) { $browserExtension['bundled_version'] = $bundledExtensionVersion }
  if (-not [string](Get-MapValue $browserExtension 'latest_version')) { $browserExtension['latest_version'] = $latestExtensionVersion }
  if (-not $browserExtension.ContainsKey('managed_browser_auto_load_supported')) { $browserExtension['managed_browser_auto_load_supported'] = [bool](Get-MapValue $pluginStatus 'managed_browser_auto_load_supported') }
  $statusMap['browser_extension'] = $browserExtension
  $statusMap
}

function Get-AssistantReleaseStatus {
  $stored = Read-JsonFile (Get-ReleaseStatusPath)
  if ($stored) { return Add-AssistantReleaseStatusDefaults -Status $stored }
  $pluginStatus = Get-StoredPluginStatus
  Add-AssistantReleaseStatusDefaults -Status ([ordered]@{
    ok = $true
    local_agent = [ordered]@{ current_version = $script:HelperVersion; latest_version = $script:HelperVersion; has_update = $false; downloaded = $false }
    browser_extension = [ordered]@{ current_version = [string](Get-MapValue $pluginStatus 'extension_version'); bundled_version = [string](Get-MapValue $pluginStatus 'bundled_version'); latest_version = [string](Get-MapValue $pluginStatus 'latest_version'); has_update = $false; downloaded = $false; managed_browser_auto_load_supported = [bool](Get-MapValue $pluginStatus 'managed_browser_auto_load_supported') }
    update_status = 'idle'
    idle = Test-AssistantIdle
    downloads = Get-AssistantDownloads
  })
}

function Check-AssistantUpdates {
  $manifest = Invoke-PlatformApi -Method 'GET' -Path '/local-agent/releases/latest' -TimeoutSec 15
  $pluginStatus = Get-StoredPluginStatus
  $localAgent = Get-MapValue $manifest 'local_agent'
  $browserExtension = Get-MapValue $manifest 'browser_extension'
  $latestAgentVersion = [string](Get-MapValue $localAgent 'version')
  $latestExtensionVersion = [string](Get-MapValue $browserExtension 'version')
  if (-not $latestAgentVersion) { $latestAgentVersion = [string](Get-MapValue $manifest 'suite_version') }
  if (-not $latestExtensionVersion) { $latestExtensionVersion = [string](Get-MapValue $manifest 'suite_version') }
  $currentExtensionVersion = [string](Get-MapValue $pluginStatus 'extension_version')
  $bundledExtensionVersion = [string](Get-MapValue $pluginStatus 'bundled_version')
  if (-not $currentExtensionVersion) { $currentExtensionVersion = $bundledExtensionVersion }
  $agentHasUpdate = $latestAgentVersion -and $latestAgentVersion -ne $script:HelperVersion
  $extensionHasUpdate = $latestExtensionVersion -and $currentExtensionVersion -and $latestExtensionVersion -ne $currentExtensionVersion
  $agentDownload = if ($agentHasUpdate) { Save-ReleaseDownload -ReleasePart $localAgent } else { [ordered]@{ downloaded = $false; path = ''; error = '' } }
  $extensionDownload = if ($extensionHasUpdate) { Save-ReleaseDownload -ReleasePart $browserExtension } else { [ordered]@{ downloaded = $false; path = ''; error = '' } }
  $status = [ordered]@{
    ok = $true
    checked_at = (Get-Date).ToUniversalTime().AddHours(8).ToString('s')
    local_agent = [ordered]@{
      current_version = $script:HelperVersion
      latest_version = $latestAgentVersion
      has_update = [bool]$agentHasUpdate
      downloaded = [bool](Get-MapValue $agentDownload 'downloaded')
      download_path = [string](Get-MapValue $agentDownload 'path')
      download_error = [string](Get-MapValue $agentDownload 'error')
    }
    browser_extension = [ordered]@{
      current_version = $currentExtensionVersion
      connected_version = [string](Get-MapValue $pluginStatus 'extension_version')
      bundled_version = $bundledExtensionVersion
      latest_version = $latestExtensionVersion
      has_update = [bool]$extensionHasUpdate
      downloaded = [bool](Get-MapValue $extensionDownload 'downloaded')
      download_path = [string](Get-MapValue $extensionDownload 'path')
      download_error = [string](Get-MapValue $extensionDownload 'error')
      managed_browser_auto_load_supported = [bool](Get-MapValue $pluginStatus 'managed_browser_auto_load_supported')
    }
    update_status = 'idle'
    idle = Test-AssistantIdle
    downloads = Get-AssistantDownloads
    manifest = ConvertTo-PlainHashtable $manifest
  }
  Write-JsonFile -Path (Get-ReleaseStatusPath) -Data $status
  $pluginStatus['latest_version'] = $latestExtensionVersion
  $pluginStatus['has_update'] = [bool]$extensionHasUpdate
  $pluginStatus['downloaded'] = [bool](Get-MapValue $extensionDownload 'downloaded')
  $pluginStatus['update_message'] = if ($extensionHasUpdate) { '发现新版插件，请手动更新浏览器插件。' } else { '' }
  Write-JsonFile -Path (Get-PluginStatusPath) -Data $pluginStatus
  $status
}

function Get-AssistantDownloads {
  $root = Get-AssistantDownloadsRoot
  $items = @()
  foreach ($file in @(Get-ChildItem -LiteralPath $root -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending)) {
    $items += ,[ordered]@{ file_name = $file.Name; path = $file.FullName; size_bytes = $file.Length; downloaded_at = $file.LastWriteTimeUtc.ToString('o') }
  }
  [ordered]@{ ok = $true; root = $root; items = @($items) }
}

function Test-AssistantIdle {
  $queueStatus = Get-OperationRecordingQueueStatus
  $workerStatus = Get-WorkerRuntimeStatus
  $runningCommand = [string](Get-MapValue $workerStatus 'current_command_id')
  $reasons = @()
  if ([int](Get-MapValue $queueStatus 'pending_count') -gt 0) { $reasons += 'operation_recording_queue_pending' }
  if ($runningCommand) { $reasons += 'worker_command_running' }
  if ($script:UpdateInProgress) { $reasons += 'updating' }
  [ordered]@{ ok = (@($reasons).Count -eq 0); reasons = @($reasons) }
}

function Apply-UpdateIfIdle {
  $status = Get-AssistantReleaseStatus
  if ($script:UpdateInProgress) {
    $status['update_status'] = 'updating'
    Write-JsonFile -Path (Get-ReleaseStatusPath) -Data $status
    return $status
  }
  $hasUpdate = [bool](Get-MapValue (Get-MapValue $status 'local_agent') 'has_update')
  if (-not $hasUpdate) {
    $status['update_status'] = 'no_update'
    Write-JsonFile -Path (Get-ReleaseStatusPath) -Data $status
    return $status
  }
  $idle = Test-AssistantIdle
  $status['idle'] = $idle
  if (-not [bool](Get-MapValue $idle 'ok')) {
    $status['update_status'] = 'pending_idle'
    Write-JsonFile -Path (Get-ReleaseStatusPath) -Data $status
    return $status
  }
  $script:UpdateInProgress = $true
  try {
    $status['update_status'] = 'applied_pending_restart'
    $status['applied_at'] = (Get-Date).ToUniversalTime().AddHours(8).ToString('s')
    $status['message'] = '本机助手更新包已下载；P0 不热替换当前进程，请重启本机助手完成更新。'
    Write-JsonFile -Path (Get-ReleaseStatusPath) -Data $status
    return $status
  } finally {
    $script:UpdateInProgress = $false
  }
}

function Get-WorkerRuntimeStatus {
  $settings = Get-HelperSettings
  $jobState = if ($script:WorkerRuntimeJob) { [string]$script:WorkerRuntimeJob.State } else { 'Stopped' }
  $stored = Read-JsonFile (Get-WorkerRuntimeStatusPath)
  $lastError = if ($stored) { [string](Get-MapValue $stored 'last_error') } else { '' }
  $currentCommandId = if ($stored) { [string](Get-MapValue $stored 'current_command_id') } else { $script:WorkerRuntimeCurrentCommandId }
  [ordered]@{
    ok = $true
    enabled = [bool](Get-MapValue $settings 'worker_runtime_enabled')
    status = if ($jobState -eq 'Running') { 'running' } else { 'stopped' }
    job_state = $jobState
    worker_id = [string](Get-MapValue $settings 'worker_id')
    display_name = [string](Get-MapValue $settings 'worker_display_name')
    version = $script:HelperVersion
    current_command_id = $currentCommandId
    allowed_commands = @('health_probe', 'dry_run_account_task_group', 'stage_only_account_task_group')
    forbidden_commands = @('production_run_account_task_group', '正式提交')
    last_error = $lastError
    status_path = Get-WorkerRuntimeStatusPath
  }
}

function Start-WorkerRuntime {
  $settings = Get-HelperSettings
  if (-not [bool](Get-MapValue $settings 'worker_runtime_enabled')) {
    return [ordered]@{ ok = $false; status = 'disabled'; message = 'worker_runtime_enabled=false' }
  }
  if ($script:WorkerRuntimeJob -and $script:WorkerRuntimeJob.State -eq 'Running') {
    return Get-WorkerRuntimeStatus
  }
  $baseUrl = Get-PlatformApiBaseUrl
  $workerId = [string](Get-MapValue $settings 'worker_id')
  $displayName = [string](Get-MapValue $settings 'worker_display_name')
  $slots = [Math]::Max(1, [int](Get-MapValue $settings 'worker_estimated_http_account_slots'))
  $version = $script:HelperVersion
  $statusPath = Get-WorkerRuntimeStatusPath
  $script:WorkerRuntimeCurrentCommandId = ''
  $script:WorkerRuntimeJob = Start-Job -Name 'aidp-local-worker-runtime' -ScriptBlock {
    param($BaseUrl, $WorkerId, $DisplayName, $Version, $Slots, $StatusPath)
    function Write-Status {
      param([string]$Status, [string]$CurrentCommandId = '', [string]$LastError = '')
      $payload = [ordered]@{
        ok = $true
        status = $Status
        worker_id = $WorkerId
        current_command_id = $CurrentCommandId
        last_error = $LastError
        updated_at = (Get-Date).ToUniversalTime().AddHours(8).ToString('s')
      }
      [System.IO.File]::WriteAllText($StatusPath, ($payload | ConvertTo-Json -Depth 20), [System.Text.UTF8Encoding]::new($false))
    }
    function Invoke-Api {
      param([string]$Method, [string]$Path, $Payload = $null)
      $parameters = @{ Uri = ($BaseUrl.TrimEnd('/') + $Path); Method = $Method; TimeoutSec = 20 }
      if ($null -ne $Payload) {
        $parameters.Body = ($Payload | ConvertTo-Json -Depth 60 -Compress)
        $parameters.ContentType = 'application/json;charset=UTF-8'
      }
      Invoke-RestMethod @parameters
    }
    function Complete-Command {
      param($Command, [bool]$Success, $Result)
      Invoke-Api -Method 'POST' -Path ("/workers/commands/{0}/result" -f $Command.command_id) -Payload ([ordered]@{ success = $Success; result = $Result }) | Out-Null
    }
    while ($true) {
      try {
        Write-Status -Status 'registering'
        Invoke-Api -Method 'POST' -Path '/workers/register' -Payload ([ordered]@{ worker_id = $WorkerId; display_name = $DisplayName; version = $Version; estimated_http_account_slots = $Slots }) | Out-Null
        Invoke-Api -Method 'POST' -Path '/workers/heartbeat' -Payload ([ordered]@{ worker_id = $WorkerId; display_name = $DisplayName; version = $Version }) | Out-Null
        Write-Status -Status 'polling'
        $command = $null
        try { $command = Invoke-Api -Method 'POST' -Path ("/workers/$WorkerId/commands/claim") -Payload ([ordered]@{}) } catch { $command = $null }
        if ($command -and $command.command_id) {
          $commandId = [string]$command.command_id
          $commandType = [string]$command.command_type
          Write-Status -Status 'running' -CurrentCommandId $commandId
          Invoke-Api -Method 'POST' -Path ("/workers/commands/$commandId/renew") -Payload ([ordered]@{}) | Out-Null
          if ($commandType -eq 'health_probe') {
            Complete-Command -Command $command -Success $true -Result ([ordered]@{ probe = 'ok'; worker_id = $WorkerId; helper_version = $Version; hostname = [Environment]::MachineName; writes_remote = $false; submits_remote = $false; starts_run = $false })
          } elseif ($commandType -in @('dry_run_account_task_group', 'stage_only_account_task_group')) {
            Complete-Command -Command $command -Success $true -Result ([ordered]@{ mode = $commandType; worker_id = $WorkerId; writes_remote = $false; submits_remote = $false; starts_run = $false; message = 'P0 WorkerRuntime only acknowledges safe dry-run/stage-only command envelopes.' })
          } else {
            Complete-Command -Command $command -Success $false -Result ([ordered]@{ error_code = 'UNSUPPORTED_COMMAND'; message = "WorkerRuntime blocked unsupported or production command: $commandType"; writes_remote = $false; submits_remote = $false; starts_run = $false; forbidden_commands = @('production_run_account_task_group', '正式提交') })
          }
          Write-Status -Status 'polling'
        }
      } catch {
        Write-Status -Status 'error' -LastError $_.Exception.Message
      }
      Start-Sleep -Seconds 10
    }
  } -ArgumentList $baseUrl, $workerId, $displayName, $version, $slots, $statusPath
  Get-WorkerRuntimeStatus
}

function Stop-WorkerRuntime {
  if ($script:WorkerRuntimeJob) {
    try { Stop-Job -Job $script:WorkerRuntimeJob -Force -ErrorAction SilentlyContinue } catch {}
    try { Remove-Job -Job $script:WorkerRuntimeJob -Force -ErrorAction SilentlyContinue } catch {}
    $script:WorkerRuntimeJob = $null
  }
  $script:WorkerRuntimeCurrentCommandId = ''
  [ordered]@{ ok = $true; status = 'stopped'; worker_id = [string](Get-MapValue (Get-HelperSettings) 'worker_id') }
}

function Proxy-WorkerEventToPlatform {
  param($Payload)
  $response = Invoke-PlatformApi -Method 'POST' -Path '/workers/events' -Payload $Payload -TimeoutSec 10
  [ordered]@{ ok = $true; response = ConvertTo-PlainHashtable $response }
}

function Receive-CdpMessage {
  param([Net.WebSockets.ClientWebSocket]$Socket, [int]$TimeoutMs = 30000)
  $buffer = New-Object byte[] 1048576
  $stream = New-Object IO.MemoryStream
  $cts = New-Object Threading.CancellationTokenSource
  try {
    $cts.CancelAfter($TimeoutMs)
    do {
      $segment = New-Object 'System.ArraySegment[byte]' -ArgumentList @(,$buffer)
      $result = $Socket.ReceiveAsync($segment, $cts.Token).GetAwaiter().GetResult()
      if ($result.Count -gt 0) { $stream.Write($buffer, 0, $result.Count) }
    } while (-not $result.EndOfMessage)
    ConvertFrom-JsonCompat ([Text.Encoding]::UTF8.GetString($stream.ToArray()))
  } catch [OperationCanceledException] {
    throw "Timed out receiving CDP message after $TimeoutMs ms."
  } finally {
    $cts.Dispose()
  }
}

function Send-CdpCommand {
  param([Net.WebSockets.ClientWebSocket]$Socket, [int]$Id, [string]$Method, $Params = @{})
  $payload = @{ id = $Id; method = $Method; params = $Params } | ConvertTo-Json -Depth 100 -Compress
  $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
  $segment = New-Object 'System.ArraySegment[byte]' -ArgumentList @(,$bytes)
  $Socket.SendAsync($segment, [Net.WebSockets.WebSocketMessageType]::Text, $true, [Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null
}

function Wait-CdpResponse {
  param([Net.WebSockets.ClientWebSocket]$Socket, [int]$Id, [int]$TimeoutMs = 30000)
  $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
  while ([DateTime]::UtcNow -lt $deadline) {
    $remaining = [int][Math]::Max(1, ($deadline - [DateTime]::UtcNow).TotalMilliseconds)
    try {
      $message = Receive-CdpMessage $Socket $remaining
      if ($message -and $message['id'] -eq $Id) { return $message }
    } catch {
      $errorText = [string]$_.Exception.Message
      if ($errorText -match 'Access-Control|Server-Timing|server-timing|duplicate|重复') { continue }
      throw
    }
  }
  throw "Timed out waiting for CDP response: $Id"
}

function Receive-CdpUntil {
  param([Net.WebSockets.ClientWebSocket]$Socket, [int]$TimeoutMs = 30000)
  $messages = @()
  $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
  while ([DateTime]::UtcNow -lt $deadline) {
    $remaining = [int][Math]::Max(1, [Math]::Min(1000, ($deadline - [DateTime]::UtcNow).TotalMilliseconds))
    try {
      $message = Receive-CdpMessage $Socket $remaining
      if ($message) { $messages += $message }
    } catch {
      $errorText = [string]$_.Exception.Message
      if ($errorText -match 'Timed out receiving CDP message|Access-Control|Server-Timing|server-timing|duplicate|重复') { continue }
      throw
    }
  }
  $messages
}

function Get-CdpJsonResponseBody {
  param([Net.WebSockets.ClientWebSocket]$Socket, [int]$CommandId, [string]$RequestId)
  Send-CdpCommand $Socket $CommandId 'Network.getResponseBody' @{ requestId = $RequestId }
  $bodyResponse = Wait-CdpResponse $Socket $CommandId 10000
  $body = [string]$bodyResponse['result']['body']
  if (-not $body) { return $null }
  ConvertFrom-JsonCompat $body
}

function Get-CdpAidpPage {
  param([int]$CdpPort)
  $pages = Invoke-RestMethod -Uri "http://127.0.0.1:$CdpPort/json" -TimeoutSec 5
  $page = @($pages | Where-Object { $_.type -eq 'page' -and $_.url -like 'https://aidp.juejin.cn/*' -and $_.url -notlike '*/mark-v3/*' } | Select-Object -First 1)[0]
  if (-not $page) { throw "No AIDP page is open on port $CdpPort." }
  $page
}

function Read-AidpSessionFromCdp {
  param([int]$CdpPort)
  $page = Get-CdpAidpPage -CdpPort $CdpPort
  $socket = New-Object Net.WebSockets.ClientWebSocket
  try {
    $socket.ConnectAsync([Uri]([string]$page.webSocketDebuggerUrl), [Threading.CancellationToken]::None).GetAwaiter().GetResult()
    $id = 1
    Send-CdpCommand $socket $id 'Network.enable' @{}
    Wait-CdpResponse $socket $id 30000 | Out-Null
    $id++
    Send-CdpCommand $socket $id 'Network.getCookies' @{ urls = @('https://aidp.juejin.cn/') }
    $cookieResponse = Wait-CdpResponse $socket $id 30000
    $cookies = @($cookieResponse['result']['cookies']) | ForEach-Object { "$($_['name'])=$($_['value'])" }
    if (-not $cookies.Count) { throw 'No AIDP cookies were read.' }
    $id++
    $expr = @'
(async () => {
  const idPattern = /^\d{12,24}$/;
  const namePattern = /^(用户[0-9A-Za-z_-]{4,}|user[0-9A-Za-z_-]{4,})$/i;
  const idKeys = /^(userId|user_id|uid|UserID|UserId|id|openId|open_id)$/i;
  const nameKeys = /^(userName|username|UserName|Username|nickName|nickname|displayName|screenName|name|Name)$/i;
  const badName = /^(AIDP|AI|task|login|logout|home|profile|account|AI数据服务平台|数据服务平台|任务页|个人中心|登录|退出|首页)$/i;
  const ids = new Map();
  const names = new Map();
  const pairs = [];
  const cleanName = value => {
    const text = String(value ?? '').trim().replace(/\s+/g, '');
    if (!text || text.length > 64 || badName.test(text) || /^[{[]/.test(text)) return '';
    return text;
  };
  const cleanId = value => {
    const text = String(value ?? '').trim();
    return idPattern.test(text) ? text : '';
  };
  const addId = (value, score = 1, source = '') => {
    const id = cleanId(value);
    if (!id) return;
    const prev = ids.get(id) || { score: 0, count: 0, sources: [] };
    prev.score += score;
    prev.count += 1;
    if (source) prev.sources.push(source);
    ids.set(id, prev);
  };
  const addName = (value, score = 1, source = '') => {
    const name = cleanName(value);
    if (!name) return;
    const prev = names.get(name) || { score: 0, count: 0, sources: [] };
    prev.score += score;
    prev.count += 1;
    if (source) prev.sources.push(source);
    names.set(name, prev);
  };
  const addPair = (idValue, nameValue, score = 1, source = '') => {
    const id = cleanId(idValue);
    const name = cleanName(nameValue);
    if (!id && !name) return;
    if (id) addId(id, score, source);
    if (name) addName(name, score, source);
    if (id && name) pairs.push({ id, name, score, source });
  };
  const directValue = (obj, matcher) => {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return '';
    for (const key of Object.keys(obj)) {
      if (matcher.test(key)) return obj[key];
    }
    return '';
  };
  const scan = (value, key = '', depth = 0, source = '') => {
    if (value == null || depth > 7) return;
    if (typeof value === 'string' || typeof value === 'number') {
      if (idKeys.test(key)) addId(value, 80, `${source}:${key}`);
      if (nameKeys.test(key)) addName(value, 80, `${source}:${key}`);
      return;
    }
    if (Array.isArray(value)) {
      value.slice(0, 120).forEach(item => scan(item, key, depth + 1, source));
      return;
    }
    if (typeof value === 'object') {
      addPair(directValue(value, idKeys), directValue(value, nameKeys), 160 - depth * 5, source || key || 'object');
      Object.keys(value).slice(0, 220).forEach(k => scan(value[k], k, depth + 1, source || key || 'object'));
    }
  };
  const scanStorage = storage => {
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i) || '';
      const raw = storage.getItem(key) || '';
      const keyMatch = key.match(/__aidp_storage__(\d{12,24})(?=$|[_:-])/);
      const isUiStateKey = /_collapsed(?:$|[_:-])/i.test(key);
      if (keyMatch && !isUiStateKey) addId(keyMatch[1], 80, 'storage-key');
      if (!/user|account|profile|aidp|storage|passport/i.test(key)) continue;
      try {
        const parsed = JSON.parse(raw);
        if (keyMatch && !isUiStateKey) addPair(keyMatch[1], directValue(parsed, nameKeys), 120, 'storage-key+value');
        scan(parsed, key, 0, 'storage-json');
      } catch (_) {
        addName(raw, 2, 'storage-text');
      }
    }
  };
  try { scanStorage(localStorage); } catch (_) {}
  try { scanStorage(sessionStorage); } catch (_) {}
  try {
    const resourceUrls = performance.getEntriesByType('resource').map(entry => entry.name || '').filter(Boolean);
    for (const urlText of resourceUrls) {
      const talentMatch = urlText.match(/\/user\/seed\/talent_info\?[^#]*\bUserID=(\d{12,24})/i);
      if (talentMatch) addId(talentMatch[1], 280, 'authority-url:talent_info');
    }
    const signedInfoUrls = Array.from(new Set(resourceUrls.filter(url => /\/api\/crowdsourcing\/(GetUserInfo|UpdateOrCreateUserInfo)\?/i.test(url)))).slice(-6);
    for (const url of signedInfoUrls) {
      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 3500);
        const response = await fetch(url, { credentials: 'include', signal: controller.signal });
        clearTimeout(timer);
        if (!response.ok) continue;
        const data = await response.json();
        const user = data && data.User ? data.User : null;
        if (user) addPair(user.UserID || user.UserId || user.userId, user.Username || user.UserName || user.username || user.name, 520, 'authority-fetch:GetUserInfo');
        scan(data, 'GetUserInfo', 0, 'authority-fetch:GetUserInfo');
      } catch (_) {}
    }
  } catch (_) {}
  const endpointCandidates = ['/api/user/info', '/api/user', '/api/passport/web/user/info', '/passport/web/user/info'];
  for (const path of endpointCandidates) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 2500);
      const response = await fetch(path, { credentials: 'include', signal: controller.signal });
      clearTimeout(timer);
      if (!response.ok) continue;
      const data = await response.json();
      scan(data, path, 0, 'fetch');
    } catch (_) {}
  }
  try {
    document.querySelectorAll('[class*=user i], [class*=account i], [class*=name i], [class*=avatar i], [title]').forEach(el => {
      addName(el.textContent, 1, 'dom');
      addName(el.getAttribute('title'), 1, 'dom-title');
    });
  } catch (_) {}
  const pairScores = new Map();
  for (const pair of pairs) {
    const key = `${pair.id}\u0000${pair.name}`;
    const prev = pairScores.get(key) || { id: pair.id, name: pair.name, score: 0, sources: [] };
    prev.score += pair.score;
    if (pair.source) prev.sources.push(pair.source);
    pairScores.set(key, prev);
  }
  const pairCandidates = Array.from(pairScores.values()).sort((a, b) => b.score - a.score || a.id.length - b.id.length);
  const idCandidates = Array.from(ids.entries()).map(([id, meta]) => ({ id, score: meta.score, count: meta.count, sources: meta.sources.slice(0, 8) }));
  idCandidates.sort((a, b) => b.score - a.score || b.count - a.count || a.id.length - b.id.length);
  const nameCandidates = Array.from(names.entries()).map(([name, meta]) => ({ name, score: meta.score, count: meta.count, sources: meta.sources.slice(0, 8) }));
  nameCandidates.sort((a, b) => b.score - a.score || b.count - a.count || a.name.length - b.name.length);
  const idsOnly = idCandidates.map(x => x.id);
  let userId = pairCandidates.find(x => (x.sources || []).some(source => /authority-fetch:GetUserInfo|authority-url:talent_info/.test(source)))?.id || pairCandidates[0]?.id || idsOnly[0] || '';
  for (const shorter of idsOnly.slice().sort((a, b) => a.length - b.length)) {
    const longer = idsOnly.find(x => x !== shorter && x.startsWith(shorter) && x.length <= shorter.length + 3);
    if (longer) { userId = shorter; break; }
  }
  const pairedName = pairCandidates.find(x => x.id === userId && namePattern.test(x.name) && (x.sources || []).some(source => /authority-fetch:GetUserInfo/.test(source)))?.name || pairCandidates.find(x => x.id === userId && namePattern.test(x.name))?.name || '';
  const name = pairedName || nameCandidates.find(x => namePattern.test(x.name))?.name || '';
  const authoritativePair = pairCandidates.find(x => x.id === userId && (x.sources || []).some(source => /authority-fetch:GetUserInfo/.test(source))) || null;
  return { userId, authoritativeUserId: authoritativePair?.id || '', userIdCandidates: idCandidates, name, authoritativeName: authoritativePair?.name || '', userInfoSource: authoritativePair ? 'GetUserInfo' : '', nameCandidates, userPairs: pairCandidates.slice(0, 12), title: document.title || '', href: location.href };
})()
'@
    Send-CdpCommand $socket $id 'Runtime.evaluate' @{ expression = $expr; returnByValue = $true; awaitPromise = $true }
    $infoResponse = Wait-CdpResponse $socket $id 30000
    $info = $infoResponse['result']['result']['value']
    if (-not [string]$info['authoritativeName']) {
      $id++
      Send-CdpCommand $socket $id 'Page.enable' @{}
      Wait-CdpResponse $socket $id 10000 | Out-Null
      $id++
      Send-CdpCommand $socket $id 'Page.navigate' @{ url = 'https://aidp.juejin.cn/operation/lite/setting/account/personal-center?org=AIDP%20Coding&tab=2' }
      Wait-CdpResponse $socket $id 10000 | Out-Null
      $networkMessages = Receive-CdpUntil $socket 15000
      foreach ($message in @($networkMessages)) {
        if ([string]$message['method'] -ne 'Network.responseReceived') { continue }
        $params = $message['params']
        $response = $params['response']
        $url = [string]$response['url']
        if ($url -notmatch '/api/crowdsourcing/(GetUserInfo|UpdateOrCreateUserInfo)') { continue }
        try {
          $id++
          $bodyJson = Get-CdpJsonResponseBody $socket $id ([string]$params['requestId'])
          $user = if ($bodyJson -and $bodyJson['User']) { $bodyJson['User'] } else { $null }
          $bodyUserId = if ($user) { [string]$user['UserID'] } else { '' }
          $bodyName = if ($user) { [string]$user['Username'] } else { '' }
          if ($bodyUserId -match '^\d{12,24}$' -and $bodyName) {
            $info['userId'] = $bodyUserId
            $info['authoritativeUserId'] = $bodyUserId
            $info['name'] = $bodyName
            $info['authoritativeName'] = $bodyName
            $info['userInfoSource'] = 'GetUserInfoNetwork'
            break
          }
        } catch {}
      }
      $id++
      Send-CdpCommand $socket $id 'Runtime.evaluate' @{ expression = $expr; returnByValue = $true; awaitPromise = $true }
      $retryInfoResponse = Wait-CdpResponse $socket $id 30000
      $retryInfo = $retryInfoResponse['result']['result']['value']
      if ([string]$retryInfo['authoritativeName']) { $info = $retryInfo }
    }
    [ordered]@{ cookie = ($cookies -join '; '); userId = [string]$info['userId']; authoritativeUserId = [string]$info['authoritativeUserId']; userIdCandidates = @($info['userIdCandidates']); name = [string]$info['name']; authoritativeName = [string]$info['authoritativeName']; userInfoSource = [string]$info['userInfoSource']; nameCandidates = @($info['nameCandidates']); userPairs = @($info['userPairs']); title = [string]$info['title']; href = [string]$info['href']; referer = [string]$page.url }
  } finally {
    $socket.Dispose()
  }
}

function Sync-AidpSessionToMonitor {
  param([int]$CdpPort, [string]$MonitorUrl, [string]$LoginSessionId)
  if (-not $MonitorUrl) { throw 'Missing monitorUrl.' }
  $session = Read-AidpSessionFromCdp -CdpPort $CdpPort
  $payload = [ordered]@{
    cookie = [string]$session.cookie
    userId = [string]$session.userId
    userIdCandidates = @($session.userIdCandidates)
    name = [string]$session.name
    authoritativeUserId = [string]$session.authoritativeUserId
    authoritativeName = [string]$session.authoritativeName
    userInfoSource = [string]$session.userInfoSource
    title = [string]$session.title
    href = [string]$session.href
    referer = [string]$session.referer
    cdpPort = $CdpPort
  }
  $payload['loginSessionId'] = $LoginSessionId
  $payload['syncedFrom'] = 'aidp-local-helper-cdp'
  $payload['syncedAt'] = (Get-Date).ToUniversalTime().ToString('o')
  $body = $payload | ConvertTo-Json -Depth 20
  $url = $MonitorUrl.TrimEnd('/') + '/api/client-session'
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $result = Invoke-JsonPostUtf8 -Uri $url -Headers @{} -JsonBody $body -TimeoutSec 20
  } catch {
    $inner = if ($_.Exception.InnerException) { [string]$_.Exception.InnerException.Message } else { '' }
    throw "同步到监控服务失败：$($_.Exception.Message); inner=$inner; url=$url; cdpPort=$CdpPort; userId=$($payload['userId']); name=$($payload['name']); candidateCount=$(@($payload['userIdCandidates']).Count)"
  }
  [ordered]@{ ok = $true; monitor = $result; cdpPort = $CdpPort; userId = [string]$payload['userId']; cookieLength = ([string]$payload['cookie']).Length }
}

function Get-FreeCdpPort {
  $used = Get-UsedCdpPorts
  for ($port = 9323; $port -le 9422; $port++) {
    if (-not $used.ContainsKey($port)) { return $port }
  }
  throw 'No temporary CDP port is available in 9323-9422.'
}

function Convert-CookieHeaderToCdpCookies {
  param([string]$Cookie)
  $items = @()
  foreach ($part in $Cookie -split ';') {
    $text = $part.Trim()
    if (-not $text -or $text -notmatch '=') { continue }
    $eq = $text.IndexOf('=')
    $name = $text.Substring(0, $eq).Trim()
    $value = $text.Substring($eq + 1)
    if ($name) { $items += [ordered]@{ name = $name; value = $value; domain = '.juejin.cn'; path = '/'; secure = $true; httpOnly = $false } }
  }
  $items
}

function Wait-CdpPage {
  param([int]$CdpPort, [int]$TimeoutSec = 20)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  do {
    try {
      $pages = Invoke-RestMethod -Uri "http://127.0.0.1:$CdpPort/json" -TimeoutSec 2
      $page = @($pages | Where-Object { $_.type -eq 'page' } | Select-Object -First 1)[0]
      if ($page -and $page.webSocketDebuggerUrl) { return $page }
    } catch {}
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)
  throw "Temporary browser CDP port $CdpPort is not ready."
}

function Find-InjectedProfilePort {
  param([string]$ProfilePath)
  $normalizedProfile = ([System.IO.Path]::GetFullPath($ProfilePath)).TrimEnd('\').ToLowerInvariant()
  if ($script:InjectedProfilePorts.ContainsKey($normalizedProfile)) {
    $cachedPort = [int]$script:InjectedProfilePorts[$normalizedProfile]
    try {
      $version = Invoke-RestMethod -Uri "http://127.0.0.1:$cachedPort/json/version" -TimeoutSec 1
      if ([string]$version.webSocketDebuggerUrl) { return $cachedPort }
    } catch {
      $script:InjectedProfilePorts.Remove($normalizedProfile)
    }
  }
  foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe'" -ErrorAction SilentlyContinue)) {
    $commandLine = [string]$process.CommandLine
    if (-not $commandLine) { continue }
    $profileMatch = [regex]::Match($commandLine, '--user-data-dir=(?:"([^"]+)"|([^\s]+))')
    if (-not $profileMatch.Success) { continue }
    $profileArg = if ($profileMatch.Groups[1].Success) { $profileMatch.Groups[1].Value } else { $profileMatch.Groups[2].Value }
    try { $processProfile = ([System.IO.Path]::GetFullPath($profileArg)).TrimEnd('\').ToLowerInvariant() } catch { continue }
    if ($processProfile -ne $normalizedProfile) { continue }
    $portMatch = [regex]::Match($commandLine, '--remote-debugging-port=(\d+)')
    if (-not $portMatch.Success) { continue }
    $port = [int]$portMatch.Groups[1].Value
    try {
      $version = Invoke-RestMethod -Uri "http://127.0.0.1:$port/json/version" -TimeoutSec 1
      if ([string]$version.webSocketDebuggerUrl) {
        $script:InjectedProfilePorts[$normalizedProfile] = $port
        return $port
      }
    } catch {}
  }
  0
}

function Open-AidpWithInjectedCookie {
  param([string]$MonitorUrl, [string]$Token)
  if (-not $MonitorUrl) { throw 'Missing monitorUrl.' }
  if (-not $Token) { throw 'Missing token.' }
  $sessionUrl = $MonitorUrl.TrimEnd('/') + '/api/browser-open-session?token=' + [uri]::EscapeDataString($Token)
  $session = Invoke-RestMethod -Uri $sessionUrl -TimeoutSec 15
  if (-not $session.ok) { throw 'Failed to consume open token.' }
  $safeUserId = if ($session.userId) { ([string]$session.userId) -replace '[^0-9A-Za-z_.-]', '_' } else { 'cookie-open' }
  $safeTarget = if ($session.target) { ([string]$session.target) -replace '[^0-9A-Za-z_.-]', '_' } else { 'task' }
  $profilePath = Join-Path $PSScriptRoot "profiles/view-$safeUserId-$safeTarget"
  New-Item -ItemType Directory -Force -Path $profilePath | Out-Null
  $cdpPort = Find-InjectedProfilePort -ProfilePath $profilePath
  $reused = $cdpPort -gt 0
  if (-not $reused) {
    $cdpPort = Get-FreeCdpPort
    $edge = Get-EdgePath
    $bootstrapUrl = 'about:blank'
    $arguments = @("--remote-debugging-port=$cdpPort", "--user-data-dir=$profilePath")
    $arguments += Get-ManagedBrowserExtensionArguments
    $arguments += @('--no-first-run', '--no-default-browser-check', $bootstrapUrl)
    Start-Process -FilePath $edge -ArgumentList $arguments | Out-Null
  }
  $script:InjectedProfilePorts[([System.IO.Path]::GetFullPath($profilePath)).TrimEnd('\').ToLowerInvariant()] = $cdpPort
  $page = Wait-CdpPage -CdpPort $cdpPort -TimeoutSec 25
  $socket = New-Object Net.WebSockets.ClientWebSocket
  try {
    $socket.ConnectAsync([Uri]([string]$page.webSocketDebuggerUrl), [Threading.CancellationToken]::None).GetAwaiter().GetResult()
    $id = 1
    Send-CdpCommand $socket $id 'Network.enable' @{}; Wait-CdpResponse $socket $id 30000 | Out-Null; $id++
    if (-not $reused) {
      Send-CdpCommand $socket $id 'Storage.clearDataForOrigin' @{ origin = 'https://aidp.juejin.cn'; storageTypes = 'all' }; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
      Send-CdpCommand $socket $id 'Network.clearBrowserCookies' @{}; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
    }
    $cookies = @(Convert-CookieHeaderToCdpCookies -Cookie ([string]$session.cookie))
    if (-not $cookies.Count) { throw 'Docker cookies are empty or invalid.' }
    Send-CdpCommand $socket $id 'Network.setCookies' @{ cookies = $cookies }; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
    Send-CdpCommand $socket $id 'Page.enable' @{}; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
    Send-CdpCommand $socket $id 'Page.navigate' @{ url = [string]$session.targetUrl }; Wait-CdpResponse $socket $id 10000 | Out-Null
  } finally {
    $socket.Dispose()
  }
  $extensionInfo = Get-BundledBrowserExtensionInfo
  [ordered]@{ ok = $true; userId = [string]$session.userId; target = [string]$session.target; targetUrl = [string]$session.targetUrl; cdpPort = $cdpPort; profilePath = $profilePath; injectedCookieCount = @($cookies).Count; reused = $reused; extensionAutoLoadSupported = [bool](Get-MapValue $extensionInfo 'managed_browser_auto_load_supported'); extensionDirectory = [string](Get-MapValue $extensionInfo 'extension_directory') }
}

function Get-RequestListValue {
  param($Body, [string]$Key)
  $value = Get-MapValue $Body $Key
  if ($null -eq $value) { return @() }
  if ($value -is [string]) { return @([string]$value) }
  $items = @()
  foreach ($item in @($value)) {
    $text = ([string]$item).Trim()
    if ($text) { $items += $text }
  }
  $items
}

function Get-SafeDomainList {
  param([string]$HtmlUrl, $Body)
  $domains = @()
  foreach ($item in @(Get-RequestListValue -Body $Body -Key 'allowed_domains')) {
    $domain = ([string]$item).Trim().ToLowerInvariant()
    $domain = $domain -replace '^https?://', ''
    $domain = ($domain -split '/')[0]
    $domain = ($domain -split ':')[0]
    if ($domain -and -not $domains.Contains($domain)) { $domains += $domain }
  }
  try {
    $uri = [Uri]$HtmlUrl
    $htmlHost = $uri.Host.ToLowerInvariant()
    if ($htmlHost -and -not $domains.Contains($htmlHost)) { $domains += $htmlHost }
  } catch {}
  $domains
}

function Test-SandboxUrlAllowed {
  param([string]$Url, [string[]]$AllowedDomains)
  if (-not $Url) { return $false }
  try {
    $uri = [Uri]$Url
    if ($uri.Scheme -notin @('http', 'https')) { return $false }
    $targetHost = $uri.Host.ToLowerInvariant()
    if ($targetHost -eq 'aidp.juejin.cn' -or $targetHost.EndsWith('.aidp.juejin.cn')) { return $false }
    if (-not $AllowedDomains -or -not $AllowedDomains.Count) { return $true }
    foreach ($domain in @($AllowedDomains)) {
      $safeDomain = ([string]$domain).ToLowerInvariant()
      if ($targetHost -eq $safeDomain -or $targetHost.EndsWith(".$safeDomain")) { return $true }
    }
    return $false
  } catch {
    return $false
  }
}

function New-SandboxClickExpression {
  param([string]$Selector, [int]$WaitMs)
  $inputJson = @{ selector = $Selector; waitMs = $WaitMs } | ConvertTo-Json -Depth 10 -Compress
  $inputLiteral = $inputJson | ConvertTo-Json -Compress
  $template = @'
(async () => {
  const input = JSON.parse(__INPUT_LITERAL__);
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const result = {
    selector: input.selector,
    status: "not_found",
    beforeUrl: String(location.href || ""),
    afterUrl: String(location.href || ""),
    urlChanged: false,
    domChanged: false,
    popupDetected: false,
    animationDetected: false,
    interactionDetected: false,
    evidence: "",
    error: ""
  };
  const beforeHtml = document.documentElement ? document.documentElement.outerHTML : "";
  const beforeAnimations = document.getAnimations ? document.getAnimations().length : 0;
  const previousOpen = window.open;
  window.__aidpSandboxPopups = [];
  window.open = function(url) {
    window.__aidpSandboxPopups.push(String(url || ""));
    return null;
  };
  try {
    const el = document.querySelector(input.selector);
    if (!el) {
      result.evidence = "selector 未匹配到元素。";
      return result;
    }
    result.status = "clicked";
    const href = el.tagName && el.tagName.toLowerCase() === "a" ? el.getAttribute("href") : "";
    if (href) {
      result.afterUrl = new URL(href, location.href).href;
      result.urlChanged = result.beforeUrl !== result.afterUrl;
      result.interactionDetected = result.urlChanged;
      result.evidence = result.urlChanged ? "URL 变化" : "链接点击未改变 URL";
      el.click();
      return result;
    }
    el.scrollIntoView({ block: "center", inline: "center" });
    await wait(100);
    const style = getComputedStyle(el);
    const styleAnimation = `${style.animationName || ""} ${style.animationDuration || ""} ${style.transitionDuration || ""}`;
    try {
      el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, cancelable: true, view: window }));
      el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
      el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
      el.click();
    } catch (error) {
      result.error = String(error && error.message ? error.message : error);
    }
    await wait(Math.max(100, Math.min(Number(input.waitMs || 1000), 5000)));
    const afterHtml = document.documentElement ? document.documentElement.outerHTML : "";
    const afterAnimations = document.getAnimations ? document.getAnimations().filter((animation) => animation.playState === "running" || Number(animation.currentTime || 0) > 0).length : 0;
    result.afterUrl = String(location.href || "");
    result.urlChanged = result.beforeUrl !== result.afterUrl;
    result.domChanged = beforeHtml !== afterHtml;
    result.popupDetected = Array.isArray(window.__aidpSandboxPopups) && window.__aidpSandboxPopups.length > 0;
    result.animationDetected = afterAnimations > beforeAnimations || /(^|\s)(?!0s)(?!0ms)\d+(\.\d+)?m?s/.test(styleAnimation);
    result.interactionDetected = result.urlChanged || result.domChanged || result.popupDetected || result.animationDetected;
    const evidence = [];
    if (result.urlChanged) evidence.push("URL 变化");
    if (result.domChanged) evidence.push("DOM 变化");
    if (result.popupDetected) evidence.push("弹窗/新窗口信号");
    if (result.animationDetected) evidence.push("动画/transition 信号");
    result.evidence = evidence.length ? evidence.join("；") : "点击后未观察到明显变化。";
    return result;
  } finally {
    window.open = previousOpen;
  }
})()
'@
  $template.Replace('__INPUT_LITERAL__', $inputLiteral)
}

function Wait-SandboxPageReady {
  param([Net.WebSockets.ClientWebSocket]$Socket, [int]$CommandId, [int]$TimeoutMs)
  $expr = @"
new Promise((resolve) => {
  const done = () => resolve(document.readyState || "");
  if (document.readyState === "complete" || document.readyState === "interactive") {
    resolve(document.readyState);
  } else {
    window.addEventListener("load", done, { once: true });
    setTimeout(done, Math.max(500, Math.min($TimeoutMs, 5000)));
  }
})
"@
  Send-CdpCommand $Socket $CommandId 'Runtime.evaluate' @{ expression = $expr; returnByValue = $true; awaitPromise = $true }
  Wait-CdpResponse $Socket $CommandId ([Math]::Max(1000, $TimeoutMs)) | Out-Null
}

function Invoke-SandboxClickExecution {
  param($Body)
  $started = Get-Date
  $htmlUrl = ([string](Get-MapValue $Body 'html_url')).Trim()
  if (-not $htmlUrl) { $htmlUrl = ([string](Get-MapValue $Body 'htmlUrl')).Trim() }
  $selectors = @(Get-RequestListValue -Body $Body -Key 'selectors')
  $maxClicks = [int]((Get-MapValue $Body 'max_clicks') -as [int])
  if (-not $maxClicks) { $maxClicks = [int]((Get-MapValue $Body 'maxClicks') -as [int]) }
  if (-not $maxClicks) { $maxClicks = 3 }
  $maxClicks = [Math]::Max(1, [Math]::Min(10, $maxClicks))
  $timeoutMs = [int]((Get-MapValue $Body 'timeout_ms') -as [int])
  if (-not $timeoutMs) { $timeoutMs = [int]((Get-MapValue $Body 'timeoutMs') -as [int]) }
  if (-not $timeoutMs) { $timeoutMs = 5000 }
  $timeoutMs = [Math]::Max(500, [Math]::Min(15000, $timeoutMs))
  $allowedDomains = @(Get-SafeDomainList -HtmlUrl $htmlUrl -Body $Body)
  if (-not (Test-SandboxUrlAllowed -Url $htmlUrl -AllowedDomains $allowedDomains)) {
    throw "Sandbox click execution only allows non-AIDP question web URLs within allowed_domains. htmlUrl=$htmlUrl; allowedDomains=$($allowedDomains -join ',')"
  }
  if (-not $selectors.Count) { throw 'Missing selectors.' }

  $profilePath = Join-Path ([IO.Path]::GetTempPath()) ("aidp-sandbox-click-{0}-{1}" -f (Get-Date -Format 'yyyyMMddHHmmssfff'), ([Guid]::NewGuid().ToString('N').Substring(0, 8)))
  New-Item -ItemType Directory -Force -Path $profilePath | Out-Null
  $cdpPort = Get-FreeCdpPort
  $edge = Get-EdgePath
  $process = $null
  $socket = $null
  $results = @()
  try {
    $arguments = @(
      "--remote-debugging-port=$cdpPort",
      "--user-data-dir=$profilePath",
      '--headless=new',
      '--disable-gpu',
      '--no-first-run',
      '--no-default-browser-check',
      'about:blank'
    )
    $process = Start-Process -FilePath $edge -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $page = Wait-CdpPage -CdpPort $cdpPort -TimeoutSec ([Math]::Max(10, [int]($timeoutMs / 1000) + 5))
    $socket = New-Object Net.WebSockets.ClientWebSocket
    $socket.ConnectAsync([Uri]([string]$page.webSocketDebuggerUrl), [Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null
    $id = 1
    Send-CdpCommand $socket $id 'Page.enable' @{}; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
    Send-CdpCommand $socket $id 'Runtime.enable' @{}; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
    foreach ($selector in @($selectors | Select-Object -First $maxClicks)) {
      Send-CdpCommand $socket $id 'Page.navigate' @{ url = $htmlUrl }; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
      Wait-SandboxPageReady -Socket $socket -CommandId $id -TimeoutMs $timeoutMs; $id++
      $expr = New-SandboxClickExpression -Selector ([string]$selector) -WaitMs ([Math]::Min(5000, $timeoutMs))
      Send-CdpCommand $socket $id 'Runtime.evaluate' @{ expression = $expr; returnByValue = $true; awaitPromise = $true }
      $response = Wait-CdpResponse $socket $id ([Math]::Max(3000, $timeoutMs + 2000)); $id++
      $runtimeResult = Get-MapValue (Get-MapValue $response 'result') 'result'
      $value = Get-MapValue $runtimeResult 'value'
      if ($value) {
        $afterUrl = [string](Get-MapValue $value 'afterUrl')
        if ($afterUrl -and -not (Test-SandboxUrlAllowed -Url $afterUrl -AllowedDomains $allowedDomains)) {
          $value['evidence'] = ([string](Get-MapValue $value 'evidence')) + '；点击后跳转到允许域名之外，已在隔离浏览器中截断后续复用。'
        }
        $results += ,$value
      } else {
        $exception = Get-MapValue (Get-MapValue $response 'result') 'exceptionDetails'
        $errorText = if ($exception) { ($exception | ConvertTo-Json -Depth 20 -Compress) } else { 'CDP Runtime.evaluate returned empty value.' }
        $results += ,[ordered]@{ selector = [string]$selector; status = 'error'; error = $errorText; beforeUrl = $htmlUrl; afterUrl = $htmlUrl; urlChanged = $false; domChanged = $false; popupDetected = $false; animationDetected = $false; interactionDetected = $false; evidence = '' }
      }
    }
  } finally {
    if ($socket) { try { $socket.Dispose() } catch {} }
    if ($process -and -not $process.HasExited) { try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {} }
  }
  $summary = [ordered]@{
    hasNavigation = [bool](@($results | Where-Object { [bool](Get-MapValue $_ 'urlChanged') }).Count)
    hasDomInteraction = [bool](@($results | Where-Object { [bool](Get-MapValue $_ 'domChanged') }).Count)
    hasPopup = [bool](@($results | Where-Object { [bool](Get-MapValue $_ 'popupDetected') }).Count)
    hasAnimation = [bool](@($results | Where-Object { [bool](Get-MapValue $_ 'animationDetected') }).Count)
    clickedCount = @($results | Where-Object { [string](Get-MapValue $_ 'status') -eq 'clicked' }).Count
  }
  Add-HelperLog -Level 'info' -Event 'sandbox.click.execute' -Message '独立沙箱点击执行完成' -Data ([ordered]@{ htmlUrl = $htmlUrl; selectors = @($selectors).Count; clicked = $summary.clickedCount; elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds })
  [ordered]@{
    ok = $true
    mode = 'host_helper_sandbox_click_execution'
    htmlUrl = $htmlUrl
    allowedDomains = @($allowedDomains)
    results = @($results)
    summary = $summary
    elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds
  }
}

function New-VideoKeyframeExpression {
  param([string]$VideoUrl, [int]$FrameIndex, [int]$FrameCount)
  $safeVideoUrl = ConvertTo-JsonString $VideoUrl
  $safeFrameIndex = [Math]::Max(0, $FrameIndex)
  $safeFrameCount = [Math]::Max(1, $FrameCount)
  @"
(async () => {
  const videoUrl = $safeVideoUrl;
  const frameIndex = $safeFrameIndex;
  const frameCount = $safeFrameCount;
  document.body.innerHTML = '';
  document.body.style.margin = '0';
  document.body.style.background = '#ffffff';
  const video = document.createElement('video');
  video.src = videoUrl;
  video.muted = true;
  video.playsInline = true;
  video.preload = 'auto';
  video.controls = false;
  video.style.display = 'block';
  video.style.width = '960px';
  video.style.height = '540px';
  video.style.objectFit = 'contain';
  video.style.background = '#ffffff';
  document.body.appendChild(video);
  const waitEvent = (target, name, timeoutMs) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout waiting ' + name)), timeoutMs);
    target.addEventListener(name, () => { clearTimeout(timer); resolve(); }, { once: true });
  });
  await waitEvent(video, 'loadedmetadata', 10000);
  const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 3;
  const points = frameCount === 1 ? [Math.min(0.5, duration * 0.25)] : Array.from({ length: frameCount }, (_, index) => {
    const ratio = (index + 1) / (frameCount + 1);
    return Math.max(0.1, Math.min(duration - 0.1, duration * ratio));
  });
  const timestamp = points[Math.min(frameIndex, points.length - 1)];
  video.currentTime = timestamp;
  await waitEvent(video, 'seeked', 10000);
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  return {
    ok: true,
    timestampSec: timestamp,
    durationSec: duration,
    width: video.videoWidth || 960,
    height: video.videoHeight || 540
  };
})()
"@
}

function Invoke-VideoKeyframeExtract {
  param($Body)
  $started = Get-Date
  $resources = @()
  foreach ($item in @($Body.video_resources)) {
    $url = [string](Get-MapValue $item 'url')
    $key = [string](Get-MapValue $item 'key')
    if (-not $key) { $key = [string](Get-MapValue $item 'resource_key') }
    if ($url) { $resources += ,[ordered]@{ key = $key; url = $url } }
  }
  $maxFrames = 3
  try { if (Get-MapValue $Body 'max_frames_per_video') { $maxFrames = [int](Get-MapValue $Body 'max_frames_per_video') } } catch {}
  $maxFrames = [Math]::Max(1, [Math]::Min(5, $maxFrames))
  $timeoutMs = 12000
  try { if (Get-MapValue $Body 'timeout_ms') { $timeoutMs = [int](Get-MapValue $Body 'timeout_ms') } } catch {}
  $timeoutMs = [Math]::Max(3000, [Math]::Min(30000, $timeoutMs))
  if (-not @($resources).Count) { throw 'Missing video resources.' }
  foreach ($resource in @($resources)) {
    if (-not (Test-SandboxUrlAllowed -Url ([string]$resource.url) -AllowedDomains @())) {
      throw "Unsafe video URL: $($resource.url)"
    }
  }
  $cdpPort = Get-FreeTcpPort -Start 9350 -End 9450
  $profilePath = Join-Path $PSScriptRoot ("profiles/video-keyframe-{0}-{1}" -f $PID, ([Guid]::NewGuid().ToString('N').Substring(0, 8)))
  New-Item -ItemType Directory -Force -Path $profilePath | Out-Null
  $edge = Get-EdgePath
  $process = $null
  $socket = $null
  $results = @()
  try {
    $arguments = @(
      "--remote-debugging-port=$cdpPort",
      "--user-data-dir=$profilePath",
      '--headless=new',
      '--disable-gpu',
      '--mute-audio',
      '--no-first-run',
      '--no-default-browser-check',
      'about:blank'
    )
    $process = Start-Process -FilePath $edge -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $page = Wait-CdpPage -CdpPort $cdpPort -TimeoutSec ([Math]::Max(10, [int]($timeoutMs / 1000) + 5))
    $socket = New-Object Net.WebSockets.ClientWebSocket
    $socket.ConnectAsync([Uri]([string]$page.webSocketDebuggerUrl), [Threading.CancellationToken]::None).GetAwaiter().GetResult() | Out-Null
    $id = 1
    Send-CdpCommand $socket $id 'Page.enable' @{}; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
    Send-CdpCommand $socket $id 'Runtime.enable' @{}; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
    Send-CdpCommand $socket $id 'Emulation.setDeviceMetricsOverride' @{ width = 960; height = 540; deviceScaleFactor = 1; mobile = $false }; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
    foreach ($resource in @($resources)) {
      $frames = @()
      $resourceStatus = 'ok'
      $resourceError = ''
      try {
        Send-CdpCommand $socket $id 'Page.navigate' @{ url = 'about:blank' }; Wait-CdpResponse $socket $id 10000 | Out-Null; $id++
        Start-Sleep -Milliseconds 200
        for ($frameIndex = 0; $frameIndex -lt $maxFrames; $frameIndex++) {
          $expr = New-VideoKeyframeExpression -VideoUrl ([string]$resource.url) -FrameIndex $frameIndex -FrameCount $maxFrames
          Send-CdpCommand $socket $id 'Runtime.evaluate' @{ expression = $expr; returnByValue = $true; awaitPromise = $true }
          $response = Wait-CdpResponse $socket $id ([Math]::Max(5000, $timeoutMs + 2000)); $id++
          $runtimeResult = Get-MapValue (Get-MapValue $response 'result') 'result'
          $value = Get-MapValue $runtimeResult 'value'
          if (-not $value) { throw 'Video seek returned empty value.' }
          Send-CdpCommand $socket $id 'Page.captureScreenshot' @{ format = 'jpeg'; quality = 72; captureBeyondViewport = $false }
          $shot = Wait-CdpResponse $socket $id ([Math]::Max(5000, $timeoutMs)); $id++
          $data = [string](Get-MapValue (Get-MapValue $shot 'result') 'data')
          if (-not $data) { throw 'Page.captureScreenshot returned empty data.' }
          $frames += ,[ordered]@{
            index = $frameIndex
            timestampSec = [double](Get-MapValue $value 'timestampSec')
            dataUrl = "data:image/jpeg;base64,$data"
            width = [int](Get-MapValue $value 'width')
            height = [int](Get-MapValue $value 'height')
            mimeType = 'image/jpeg'
          }
        }
      } catch {
        $resourceStatus = 'error'
        $resourceError = $_.Exception.Message
      }
      $results += ,[ordered]@{
        resourceKey = [string]$resource.key
        url = [string]$resource.url
        status = $resourceStatus
        keyframes = @($frames)
        error = $resourceError
      }
    }
  } finally {
    if ($socket) { try { $socket.Dispose() } catch {} }
    if ($process -and -not $process.HasExited) { try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {} }
    try { Remove-Item -LiteralPath $profilePath -Recurse -Force -ErrorAction SilentlyContinue } catch {}
  }
  $ok = [bool](@($results | Where-Object { [string](Get-MapValue $_ 'status') -eq 'ok' -and @((Get-MapValue $_ 'keyframes')).Count -gt 0 }).Count -eq @($resources).Count)
  Add-HelperLog -Level 'info' -Event 'video.keyframe.extract' -Message '视频关键帧抽取完成' -Data ([ordered]@{ videos = @($resources).Count; ok = $ok; elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds })
  [ordered]@{
    ok = $ok
    mode = 'host_helper_video_keyframe_extract'
    results = @($results)
    elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds
  }
}

function Get-ConfigPath {
  $local = Join-Path $PSScriptRoot 'config/accounts.docker-local.json'
  if (Test-Path -LiteralPath $local) { return $local }
  Join-Path $PSScriptRoot 'config/accounts.json'
}

function Get-NextAccountPort {
  param($Config)
  $used = Get-UsedCdpPorts
  for ($port = 9222; $port -le 9322; $port++) {
    if (-not $used.ContainsKey($port)) { return $port }
  }
  throw 'No default CDP port is available in 9222-9322.'
}

function Add-OrUpdateAccountConfig {
  param([string]$UserId, [int]$CdpPort)
  $configPath = Get-ConfigPath
  $config = Read-JsonFile $configPath
  if (-not $config) { return $null }
  $accounts = @($config.accounts)
  $existing = @($accounts | Where-Object { [string]$_.userId -eq $UserId } | Select-Object -First 1)[0]
  if ($existing) {
    $existing.cdpPort = $CdpPort
    $existing.cdpHost = 'host.docker.internal'
    $existing.enabled = $true
  } else {
    $newAccount = [ordered]@{
      enabled = $true
      name = "New account-$UserId"
      userId = $UserId
      cookie = ''
      tasks = @()
      cdpPort = $CdpPort
      cdpHost = 'host.docker.internal'
      operationUrl = 'https://aidp.juejin.cn/operation/task-v2?page=1'
    }
    $config.accounts = @($accounts + ([pscustomobject]$newAccount))
  }
  Write-JsonFile -Path $configPath -Data $config
  $configPath
}

function Test-CdpAlive {
  param([int]$CdpPort)
  try {
    $response = Invoke-RestMethod -Uri "http://127.0.0.1:$CdpPort/json/version" -TimeoutSec 2
    return [bool]$response.webSocketDebuggerUrl
  } catch {
    return $false
  }
}

function Start-ConfiguredAidpAccounts {
  $config = Read-JsonFile (Get-ConfigPath)
  if (-not $config) { return @() }
  $results = @()
  foreach ($account in @($config.accounts)) {
    if ($account.enabled -eq $false) { continue }
    if (-not $account.cdpPort) { continue }
    $userId = [string]$account.userId
    if (-not $userId) { $userId = "account-$($account.cdpPort)" }
    $port = [int]$account.cdpPort
    if (Test-CdpAlive -CdpPort $port) {
      $results += [pscustomobject]@{ ok = $true; userId = $userId; cdpPort = $port; alreadyRunning = $true }
      continue
    }
    try {
      $started = Start-AidpProfile -UserId $userId -CdpPort $port
      $started.alreadyRunning = $false
      $results += [pscustomobject]$started
    } catch {
      $results += [pscustomobject]@{ ok = $false; userId = $userId; cdpPort = $port; error = $_.Exception.Message }
    }
  }
  $results
}
function Start-NewAidpAccount {
  $config = Read-JsonFile (Get-ConfigPath)
  $port = Get-NextAccountPort $config
  $userId = "account-$port"
  $result = Start-AidpProfile -UserId $userId -CdpPort $port
  $configPath = Add-OrUpdateAccountConfig -UserId $userId -CdpPort $port
  $result.configPath = if ($configPath) { $configPath } else { '' }
  $result.autoSaved = [bool]$configPath
  $result
}

function Start-AidpProfile {
  param([string]$UserId, [int]$CdpPort)
  if ($CdpPort -lt 1024 -or $CdpPort -gt 65535) { throw 'Port must be between 1024 and 65535.' }
  $safeUserId = if ($UserId) { $UserId -replace '[^0-9A-Za-z_.-]', '_' } else { "account-$CdpPort" }
  $profilePath = Join-Path $PSScriptRoot "profiles/$safeUserId"
  New-Item -ItemType Directory -Force -Path $profilePath | Out-Null
  $edge = Get-EdgePath
  $url = 'https://aidp.juejin.cn/operation/task-v2?page=1'
  $arguments = @(
    "--remote-debugging-port=$CdpPort",
    "--user-data-dir=$profilePath"
  )
  $arguments += Get-ManagedBrowserExtensionArguments
  $arguments += @(
    '--no-first-run',
    '--no-default-browser-check',
    $url
  )
  Start-Process -FilePath $edge -ArgumentList $arguments | Out-Null
  $extensionInfo = Get-BundledBrowserExtensionInfo
  [ordered]@{ ok = $true; userId = $safeUserId; cdpPort = $CdpPort; profilePath = $profilePath; url = $url; extensionAutoLoadSupported = [bool](Get-MapValue $extensionInfo 'managed_browser_auto_load_supported'); extensionDirectory = [string](Get-MapValue $extensionInfo 'extension_directory') }
}

$prefix = "http://${HostName}:${Port}/"
$listener = New-Object Net.HttpListener
$listener.Prefixes.Add($prefix)
$listener.Start()
Write-Host "AIDP Local Helper started: $prefix"
Add-HelperLog -Level 'info' -Event 'helper.started' -Message 'AIDP 本机助手已启动' -Data ([ordered]@{ prefix = $prefix; version = $script:HelperVersion })
try {
  if ([bool](Get-MapValue (Get-HelperSettings) 'worker_runtime_enabled')) {
    Start-WorkerRuntime | Out-Null
  }
} catch {
  Add-HelperLog -Level 'warn' -Event 'worker_runtime.start.failed' -Message ('WorkerRuntime 启动失败：' + $_.Exception.Message)
}
if ($AutoOpenAccounts) {
  $opened = Start-ConfiguredAidpAccounts
  Write-Host ("Auto-opened account browsers: {0}" -f @($opened).Count)
}
try {
  while ($listener.IsListening) {
    $context = $listener.GetContext()
    try {
      if ($context.Request.HttpMethod -eq 'OPTIONS') {
        Write-OptionsResponse $context.Response
        continue
      }
      $path = $context.Request.Url.AbsolutePath
      if ($path -eq '/' -or $path -eq '/index.html') {
        Write-HtmlResponse $context.Response (Get-AssistantConsoleHtml)
      } elseif ($path -eq '/api/open-profile') {
        $userId = [string]$context.Request.QueryString['userId']
        $cdpPort = [int]$context.Request.QueryString['port']
        if (-not $userId -or -not $cdpPort) {
          Write-JsonResponse $context.Response (Start-NewAidpAccount)
        } else {
          $result = Start-AidpProfile -UserId $userId -CdpPort $cdpPort
          $result.configPath = Add-OrUpdateAccountConfig -UserId $result.userId -CdpPort $result.cdpPort
          $result.autoSaved = [bool]$result.configPath
          Write-JsonResponse $context.Response $result
        }
      } elseif ($path -eq '/api/open-configured-profiles') {
        Write-JsonResponse $context.Response ([ordered]@{ ok = $true; accounts = @(Start-ConfiguredAidpAccounts) })
      } elseif ($path -eq '/api/open-with-cookie') {
        $monitorUrl = [string]$context.Request.QueryString['monitorUrl']
        $token = [string]$context.Request.QueryString['token']
        Write-JsonResponse $context.Response (Open-AidpWithInjectedCookie -MonitorUrl $monitorUrl -Token $token)
      } elseif ($path -eq '/api/sync-aidp-session') {
        $cdpPort = [int]$context.Request.QueryString['port']
        $monitorUrl = [string]$context.Request.QueryString['monitorUrl']
        $loginSessionId = [string]$context.Request.QueryString['loginSessionId']
        if (-not $cdpPort) { throw 'Missing port.' }
        Write-JsonResponse $context.Response (Sync-AidpSessionToMonitor -CdpPort $cdpPort -MonitorUrl $monitorUrl -LoginSessionId $loginSessionId)
      } elseif ($path -eq '/api/debug-read-session') {
        $cdpPort = [int]$context.Request.QueryString['port']
        if (-not $cdpPort) { throw 'Missing port.' }
        Write-JsonResponse $context.Response (Read-AidpSessionFromCdp -CdpPort $cdpPort)
      } elseif ($path -eq '/api/ai-score/config') {
        Write-JsonResponse $context.Response (Get-AiScoreConfig)
      } elseif ($path -eq '/api/ai-score/analyze') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        $bodyText = Get-RequestBodyText $context.Request
        if (-not $bodyText) { throw 'Missing JSON request body.' }
        $body = ConvertFrom-JsonCompat $bodyText
        Write-JsonResponse $context.Response (Invoke-AiScoreAnalysis -Body $body)
      } elseif ($path -eq '/api/ai-score/debug-screenshots') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        $bodyText = Get-RequestBodyText $context.Request
        if (-not $bodyText) { throw 'Missing JSON request body.' }
        $body = ConvertFrom-JsonCompat $bodyText
        Write-JsonResponse $context.Response (Save-AiScoreDebugScreenshots -Body $body)
      } elseif ($path -eq '/api/ai-score/debug-screenshots/file') {
        $run = ([string]$context.Request.QueryString['run']) -replace '[^0-9A-Za-z_.-]', ''
        $file = ([string]$context.Request.QueryString['file']) -replace '[^0-9A-Za-z_.-]', ''
        if (-not $run -or -not $file) { throw 'Missing debug screenshot run or file.' }
        $debugRoot = Join-Path $PSScriptRoot 'debug\ai-screenshots'
        $imagePath = Join-Path (Join-Path $debugRoot $run) $file
        $extension = [IO.Path]::GetExtension($imagePath).ToLowerInvariant()
        $contentType = if ($extension -eq '.png') { 'image/png' } elseif ($extension -eq '.webp') { 'image/webp' } else { 'image/jpeg' }
        Write-FileResponse $context.Response $imagePath $contentType
      } elseif ($path -eq '/api/recordings/upload') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        $bodyText = Get-RequestBodyText $context.Request
        if (-not $bodyText) { throw 'Missing JSON request body.' }
        $body = ConvertFrom-JsonCompat $bodyText
        Write-JsonResponse $context.Response (Receive-OperationRecordingUpload -Payload $body)
      } elseif ($path -eq '/api/recordings/retry-pending') {
        $limit = 20
        try { if ($context.Request.QueryString['limit']) { $limit = [int]$context.Request.QueryString['limit'] } } catch {}
        Write-JsonResponse $context.Response ([ordered]@{ ok = $true; queue_status = Get-OperationRecordingQueueStatus; retry = Retry-QueuedOperationRecordings -Limit $limit; platform_base_url = [string](Get-MapValue (Get-HelperSettings) 'platform_base_url') })
      } elseif ($path -eq '/api/recordings/upload-queue') {
        Write-JsonResponse $context.Response ([ordered]@{ ok = $true; queue_status = Get-OperationRecordingQueueStatus; platform_base_url = [string](Get-MapValue (Get-HelperSettings) 'platform_base_url') })
      } elseif ($path -eq '/api/recordings/upload-queue/retry') {
        $limit = 20
        try { if ($context.Request.QueryString['limit']) { $limit = [int]$context.Request.QueryString['limit'] } } catch {}
        Write-JsonResponse $context.Response ([ordered]@{ ok = $true; queue_status = Get-OperationRecordingQueueStatus; retry = Retry-QueuedOperationRecordings -Limit $limit; platform_base_url = [string](Get-MapValue (Get-HelperSettings) 'platform_base_url') })
      } elseif ($path -eq '/api/workers/events') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        $bodyText = Get-RequestBodyText $context.Request
        if (-not $bodyText) { throw 'Missing JSON request body.' }
        $body = ConvertFrom-JsonCompat $bodyText
        Write-JsonResponse $context.Response (Proxy-WorkerEventToPlatform -Payload $body)
      } elseif ($path -eq '/api/assistant/config') {
        if ($context.Request.HttpMethod -eq 'GET') {
          Write-JsonResponse $context.Response (Get-AssistantConfig)
        } elseif ($context.Request.HttpMethod -eq 'POST') {
          $bodyText = Get-RequestBodyText $context.Request
          if (-not $bodyText) { throw 'Missing JSON request body.' }
          $body = ConvertFrom-JsonCompat $bodyText
          Write-JsonResponse $context.Response (Set-AssistantConfig -Payload $body)
        } else {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
        }
      } elseif ($path -eq '/api/assistant/test-platform-connection') {
        $platformUrl = ''
        if ($context.Request.HttpMethod -eq 'POST' -and $context.Request.HasEntityBody) {
          $bodyText = Get-RequestBodyText $context.Request
          if ($bodyText) {
            $body = ConvertFrom-JsonCompat $bodyText
            $platformUrl = [string](Get-MapValue $body 'platform_url')
          }
        }
        Write-JsonResponse $context.Response (Test-PlatformConnection -PlatformUrl $platformUrl)
      } elseif ($path -eq '/api/assistant/autostart') {
        if ($context.Request.HttpMethod -eq 'GET') {
          Write-JsonResponse $context.Response (Get-AssistantAutostart)
        } elseif ($context.Request.HttpMethod -eq 'POST') {
          $bodyText = Get-RequestBodyText $context.Request
          $body = if ($bodyText) { ConvertFrom-JsonCompat $bodyText } else { @{} }
          Write-JsonResponse $context.Response (Set-AssistantAutostart -Payload $body)
        } else {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
        }
      } elseif ($path -eq '/api/assistant/diagnostics') {
        Write-JsonResponse $context.Response (Get-AssistantDiagnostics)
      } elseif ($path -eq '/api/assistant/diagnostics/run') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        Write-JsonResponse $context.Response (Invoke-AssistantDiagnostics)
      } elseif ($path -eq '/api/assistant/diagnostics/export') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        Write-JsonResponse $context.Response (Export-AssistantDiagnostics)
      } elseif ($path -eq '/api/assistant/open-folder') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        $bodyText = Get-RequestBodyText $context.Request
        $body = if ($bodyText) { ConvertFrom-JsonCompat $bodyText } else { @{} }
        Write-JsonResponse $context.Response (Open-AssistantFolder -Folder ([string](Get-MapValue $body 'folder')))
      } elseif ($path -eq '/api/assistant/release-status') {
        Write-JsonResponse $context.Response (Get-AssistantReleaseStatus)
      } elseif ($path -eq '/api/assistant/check-updates') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        Write-JsonResponse $context.Response (Check-AssistantUpdates)
      } elseif ($path -eq '/api/assistant/apply-update-if-idle') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        Write-JsonResponse $context.Response (Apply-UpdateIfIdle)
      } elseif ($path -eq '/api/assistant/downloads') {
        Write-JsonResponse $context.Response (Get-AssistantDownloads)
      } elseif ($path -eq '/api/assistant/plugin-status') {
        Write-JsonResponse $context.Response ([ordered]@{ ok = $true; plugin_status = Get-StoredPluginStatus })
      } elseif ($path -eq '/api/assistant/plugin-version') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        $bodyText = Get-RequestBodyText $context.Request
        if (-not $bodyText) { throw 'Missing JSON request body.' }
        $body = ConvertFrom-JsonCompat $bodyText
        Write-JsonResponse $context.Response (Set-PluginVersion -Payload $body)
      } elseif ($path -eq '/api/worker-runtime/status') {
        Write-JsonResponse $context.Response (Get-WorkerRuntimeStatus)
      } elseif ($path -eq '/api/worker-runtime/start') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        Write-JsonResponse $context.Response (Start-WorkerRuntime)
      } elseif ($path -eq '/api/worker-runtime/stop') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        Write-JsonResponse $context.Response (Stop-WorkerRuntime)
      } elseif ($path -eq '/api/sandbox-click-execute') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        $bodyText = Get-RequestBodyText $context.Request
        if (-not $bodyText) { throw 'Missing JSON request body.' }
        $body = ConvertFrom-JsonCompat $bodyText
        Write-JsonResponse $context.Response (Invoke-SandboxClickExecution -Body $body)
      } elseif ($path -eq '/api/video-keyframe-extract') {
        if ($context.Request.HttpMethod -ne 'POST') {
          Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Method Not Allowed' }) 405
          continue
        }
        $bodyText = Get-RequestBodyText $context.Request
        if (-not $bodyText) { throw 'Missing JSON request body.' }
        $body = ConvertFrom-JsonCompat $bodyText
        Write-JsonResponse $context.Response (Invoke-VideoKeyframeExtract -Body $body)
      } elseif ($path -eq '/api/logs' -or $path -eq '/api/diagnostics') {
        $limit = 100
        try { if ($context.Request.QueryString['limit']) { $limit = [int]$context.Request.QueryString['limit'] } } catch {}
        Write-JsonResponse $context.Response (Get-HelperLogs -Limit $limit)
      } elseif ($path -eq '/api/health') {
        $aiConfig = Get-AiScoreConfig
        $helperSettings = Get-HelperSettings
        $queueStatus = Get-OperationRecordingQueueStatus
        $workerRuntimeStatus = Get-WorkerRuntimeStatus
        $releaseStatus = Get-AssistantReleaseStatus
        Write-JsonResponse $context.Response ([ordered]@{ ok = $true; service = 'aidp-host-launcher'; version = $script:HelperVersion; startedAt = $script:HelperStartedAt; autoOpenSupported = $true; cdpSyncSupported = $true; profileRegisterSupported = $true; cookieInjectOpenSupported = $true; aiScoreSupported = $true; sandboxClickSupported = $true; videoKeyframeSupported = $true; aiScoreConfigured = $aiConfig.configured; aiScoreModel = $aiConfig.model; aiScoreProvider = $aiConfig.provider; operationRecordingUploadSupported = $true; platformBaseUrl = [string](Get-MapValue $helperSettings 'platform_base_url'); recordingUploadRetryCount = [int](Get-MapValue $helperSettings 'recording_upload_retry_count'); recordingUploadTimeoutSec = [int](Get-MapValue $helperSettings 'recording_upload_timeout_sec'); recordingUploadQueuePending = [int](Get-MapValue $queueStatus 'pending_count'); recordingUploadFailedCache = [int](Get-MapValue $queueStatus 'failed_cache_count'); workerRuntimeSupported = $true; workerRuntimeStatus = [string](Get-MapValue $workerRuntimeStatus 'status'); workerRuntimeId = [string](Get-MapValue $workerRuntimeStatus 'worker_id'); updateStatus = [string](Get-MapValue $releaseStatus 'update_status') })
      } else {
        Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = 'Not Found' }) 404
      }
    } catch {
      Write-JsonResponse $context.Response ([ordered]@{ ok = $false; error = $_.Exception.Message }) 500
    }
  }
} finally {
  $listener.Stop()
}





