param([ValidateSet('dev', 'build', 'test', 'install', 'preview')][string]$Action = 'dev')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodePath = if ($nodeCommand) { $nodeCommand.Source } else { $null }
if (-not $nodePath) {
    $runtimeRoot = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\runtimes\cua_node'
    if (Test-Path -LiteralPath $runtimeRoot) {
        $nodePath = Get-ChildItem -LiteralPath $runtimeRoot -Directory |
            ForEach-Object { Join-Path $_.FullName 'bin\node.exe' } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -Last 1
    }
}
if (-not $nodePath) { throw 'Node.js was not found. Install Node 24 LTS or add an existing Node runtime to PATH.' }
$nodeDirectory = Split-Path -Parent $nodePath
$env:PATH = $nodeDirectory + ';' + $env:PATH
$env:npm_config_cache = Join-Path $projectRoot 'storage\npm-cache'
$env:COREPACK_HOME = Join-Path $projectRoot 'storage\frontend-toolchain-cache'
$env:COREPACK_ENABLE_DOWNLOAD_PROMPT = '0'
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($Action -eq 'install') {
        $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if ($npmCommand) { & $npmCommand.Source ci --no-audit --no-fund }
        else {
            $corepack = Join-Path $nodeDirectory 'node_modules\corepack\dist\npm.js'
            if (-not (Test-Path -LiteralPath $corepack)) { throw 'npm was not found. Install npm with Node.js.' }
            & $nodePath $corepack ci --no-audit --no-fund
        }
    } elseif ($Action -eq 'test') { & $nodePath --test tests/client.test.js tests/domain.test.js }
    else {
        $vite = Join-Path $PSScriptRoot 'node_modules\vite\bin\vite.js'
        if (-not (Test-Path -LiteralPath $vite)) { throw 'Dependencies are missing. Run .\dev.ps1 install first.' }
        if ($Action -eq 'build') { & $nodePath $vite build }
        elseif ($Action -eq 'preview') { & $nodePath $vite preview --host 127.0.0.1 --port 5173 --strictPort }
        else { & $nodePath $vite --host 127.0.0.1 --port 5173 --strictPort }
    }
    if ($LASTEXITCODE -ne 0) { throw "Frontend command failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
