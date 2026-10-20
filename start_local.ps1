param(
    [ValidateSet('Backend', 'Frontend', 'All')][string]$Service = 'All',
    [switch]$CheckOnly,
    [string]$PythonPath = $env:OPERATIONS_PYTHON_PATH,
    [string]$NodePath = $env:OPERATIONS_NODE_PATH
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot

function Get-LocalResponse([string]$Url) {
    try {
        $request = [System.Net.HttpWebRequest]::Create($Url)
        $request.Proxy = $null
        $request.Timeout = 1500
        $request.ReadWriteTimeout = 1500
        $response = $request.GetResponse()
        try {
            $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
            try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
        } finally { $response.Dispose() }
    } catch { return $null }
}

function Test-ServiceReady([string]$Name) {
    if ($Name -eq 'Frontend') {
        $html = Get-LocalResponse 'http://127.0.0.1:5173'
        return $html -and $html -match '<title>Operations Workflow</title>'
    }
    try {
        $health = (Get-LocalResponse 'http://127.0.0.1:8000/health') | ConvertFrom-Json
        if ($health.status -ne 'ok') { return $false }
        $schema = (Get-LocalResponse 'http://127.0.0.1:8000/openapi.json') | ConvertFrom-Json
        return $null -ne $schema.paths.'/auth/login' -and $null -ne $schema.paths.'/tasks'
    } catch { return $false }
}

function Assert-PortAvailable([int]$Port) {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
    try { $listener.Start() }
    catch { throw "Port $Port is already in use, but the project service is not ready. Check the existing window or choose another port in the project configuration." }
    finally { $listener.Stop() }
}

function Find-Python {
    if ($PythonPath) {
        if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'The specified PythonPath does not exist.' }
        return (Resolve-Path -LiteralPath $PythonPath).Path
    }
    foreach ($relative in @('.venv\Scripts\python.exe', 'venv\Scripts\python.exe')) {
        $candidate = Join-Path $projectRoot $relative
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\Microsoft\WindowsApps\*') { return $command.Source }
    throw 'Python was not found. Install Python and add it to PATH, or run start_backend.cmd -PythonPath "D:\Python\python.exe".'
}

function Check-Backend {
    Write-Host 'Checking Python, backend dependencies, configuration and PostgreSQL...'
    $pythonExecutable = Find-Python
    & $pythonExecutable -B (Join-Path $projectRoot 'utils\check_local_backend.py') | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Backend preflight failed. Follow the message above; see README.md for first-time setup.' }
    return $pythonExecutable
}

function Check-Frontend {
    Write-Host 'Checking Node.js and frontend dependencies...'
    & (Join-Path $projectRoot 'frontend\dev.ps1') -Action dev -NodePath $NodePath -CheckOnly
    if ($LASTEXITCODE -ne 0) { throw 'Frontend preflight failed. See README.md for first-time setup.' }
}

try {
    Set-Location -LiteralPath $projectRoot
    if ($Service -eq 'Backend') {
        if (Test-ServiceReady 'Backend') {
            Write-Host 'Backend is already running at http://127.0.0.1:8000'
            exit 0
        }
        Assert-PortAvailable 8000
        # 诊断信息输出到主机，返回值仅包含可执行文件路径。
        $pythonExecutable = Check-Backend
        if ($CheckOnly) { Write-Host 'Backend preflight passed.'; exit 0 }
        Write-Host 'Backend: http://127.0.0.1:8000    API docs: http://127.0.0.1:8000/docs'
        Write-Host 'Keep this window open. Press Ctrl+C to stop the backend.'
        & $pythonExecutable -B (Join-Path $projectRoot 'run_local.py')
        exit $LASTEXITCODE
    }
    if ($Service -eq 'Frontend') {
        if (Test-ServiceReady 'Frontend') {
            Write-Host 'Frontend is already running at http://127.0.0.1:5173'
            exit 0
        }
        Assert-PortAvailable 5173
        Check-Frontend
        if ($CheckOnly) { Write-Host 'Frontend preflight passed.'; exit 0 }
        Write-Host 'Frontend: http://127.0.0.1:5173'
        Write-Host 'Keep this window open. Press Ctrl+C to stop the frontend.'
        & (Join-Path $projectRoot 'frontend\dev.ps1') -Action dev -NodePath $NodePath
        exit $LASTEXITCODE
    }

    $backendReady = Test-ServiceReady 'Backend'
    $frontendReady = Test-ServiceReady 'Frontend'
    if (-not $backendReady) { Assert-PortAvailable 8000; $null = Check-Backend }
    if (-not $frontendReady) { Assert-PortAvailable 5173; Check-Frontend }
    if ($CheckOnly) { Write-Host 'Backend and frontend checks passed.'; exit 0 }

    # 子进程继承单次指定的运行时路径，不改写系统 PATH 或项目配置。
    $previousPython = $env:OPERATIONS_PYTHON_PATH
    $previousNode = $env:OPERATIONS_NODE_PATH
    try {
        if ($PythonPath) { $env:OPERATIONS_PYTHON_PATH = $PythonPath }
        if ($NodePath) { $env:OPERATIONS_NODE_PATH = $NodePath }
        if (-not $backendReady) {
            Start-Process -FilePath (Join-Path $projectRoot 'start_backend.cmd') -WorkingDirectory $projectRoot -WindowStyle Normal
        }
        if (-not $frontendReady) {
            Start-Process -FilePath (Join-Path $projectRoot 'start_frontend.cmd') -WorkingDirectory $projectRoot -WindowStyle Normal
        }
    } finally {
        $env:OPERATIONS_PYTHON_PATH = $previousPython
        $env:OPERATIONS_NODE_PATH = $previousNode
    }
    Write-Host 'Waiting for backend and frontend. Keep both service windows open...'
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt 45) {
        if ((Test-ServiceReady 'Backend') -and (Test-ServiceReady 'Frontend')) {
            Write-Host 'Ready: http://127.0.0.1:5173'
            Start-Process -FilePath 'http://127.0.0.1:5173' -WindowStyle Normal
            exit 0
        }
        Start-Sleep -Seconds 1
    }
    throw 'Startup did not complete. Check the backend/frontend windows for errors. Open README.md for setup instructions.'
} catch {
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
}
