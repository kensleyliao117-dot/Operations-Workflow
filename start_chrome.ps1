param(
    [string]$ChromePath = '',
    [ValidateRange(0, 65535)][int]$Port = 0,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$profilePath = Join-Path $projectRoot 'storage\chrome-profile'

function Get-CdpVersion([int]$DebugPort) {
    try {
        $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$DebugPort/json/version")
        $request.Proxy = $null
        $request.Timeout = 1500
        $response = $request.GetResponse()
        try {
            $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
            try { return $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
        } finally { $response.Dispose() }
    } catch { return $null }
}

try {
    if (-not $Port) {
        $endpoint = $env:CHROME_CDP_URL
        $envPath = Join-Path $projectRoot '.env'
        if (-not $endpoint -and (Test-Path -LiteralPath $envPath)) {
            $line = Get-Content -LiteralPath $envPath -Encoding UTF8 |
                Where-Object { $_ -match '^\s*CHROME_CDP_URL\s*=' } | Select-Object -Last 1
            if ($line) { $endpoint = ($line -replace '^\s*CHROME_CDP_URL\s*=\s*', '').Trim().Trim('"', "'") }
        }
        if ($endpoint) {
            $uri = [Uri]$endpoint
            if ($uri.Scheme -notin @('http', 'https', 'ws', 'wss') -or $uri.Host -notin @('127.0.0.1', 'localhost')) {
                throw 'CHROME_CDP_URL is not a local Chrome endpoint. Configure the remote browser separately.'
            }
            $Port = $uri.Port
        } else { $Port = 9222 }
    }
    $version = Get-CdpVersion $Port
    if ($version -and $version.webSocketDebuggerUrl) {
        Write-Output "Chrome CDP is already available at http://127.0.0.1:$Port"
        exit 0
    }
    if ($CheckOnly) { throw "Chrome CDP is unavailable at http://127.0.0.1:$Port" }
    if (-not $ChromePath) {
        $candidates = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA) |
            Where-Object { $_ } | ForEach-Object { Join-Path $_ 'Google\Chrome\Application\chrome.exe' }
        $ChromePath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if (-not $ChromePath) {
            $command = Get-Command chrome.exe -ErrorAction SilentlyContinue
            if ($command) { $ChromePath = $command.Source }
        }
    }
    if (-not $ChromePath -or -not (Test-Path -LiteralPath $ChromePath -PathType Leaf)) {
        throw 'Chrome was not found. Run start_chrome.cmd -ChromePath "D:\path\chrome.exe".'
    }
    $arguments = @('--remote-debugging-address=127.0.0.1', "--remote-debugging-port=$Port",
        ('--user-data-dir="' + $profilePath + '"'), 'https://www.xiaohongshu.com')
    # 交互窗口用于登录及处理平台验证，不关闭已有浏览器。
    Start-Process -FilePath $ChromePath -ArgumentList $arguments -WindowStyle Normal
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        $version = Get-CdpVersion $Port
        if ($version -and $version.webSocketDebuggerUrl) {
            Write-Output "Chrome CDP is ready at http://127.0.0.1:$Port"
            Write-Output "Profile: $profilePath"
            Write-Output 'Log in to the platform in this window and keep it open during collection or publishing.'
            exit 0
        }
    }
    throw 'CDP did not start. Close the project Chrome window and run this script again. No browser was force-closed.'
} catch {
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
}
