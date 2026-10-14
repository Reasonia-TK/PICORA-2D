param([switch]$Browser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopExe = Join-Path $projectRoot 'ui\src-tauri\target\release\picora-2d.exe'
if (-not $Browser -and (Test-Path -LiteralPath $desktopExe)) {
    Start-Process -FilePath $desktopExe
    exit
}
$backendRoot = Join-Path $projectRoot 'backend'
$runtimeRoot = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache\uv'
$env:CUPY_CACHE_DIR = Join-Path $projectRoot '.cache\cupy'
$env:NUMBA_CACHE_DIR = Join-Path $projectRoot '.cache\numba'
$env:PICORA_XS_REGISTRY = Join-Path $projectRoot 'data\cross_sections.json'
$pythonExe = Join-Path $backendRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe) -or -not (Test-Path -LiteralPath (Join-Path $projectRoot 'ui\node_modules'))) {
    throw 'scripts\setup.ps1を先に実行してください。'
}
try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 2 } catch { $health = $null }
if ($health -and $health.name -ne 'PICORA-2D') { throw 'ポート8766を別のアプリが使用しています。' }
if (-not $health) {
    $backendProcess = Start-Process -FilePath (Get-Command uv).Source -ArgumentList @('--directory', ('"{0}"' -f $backendRoot), 'run','--no-sync','python','run_server.py','--port','8766') -WorkingDirectory $backendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeRoot 'backend.out.log') -RedirectStandardError (Join-Path $runtimeRoot 'backend.err.log') -PassThru
    $backendProcess.Id | Set-Content -LiteralPath (Join-Path $runtimeRoot 'backend.pid')
}
$frontendReady = $false
try { $response = Invoke-WebRequest -Uri 'http://127.0.0.1:1422' -TimeoutSec 2 -UseBasicParsing; $frontendReady = $response.Content -match 'PICORA-2D' } catch { }
if (-not $frontendReady) {
    $nodeExe = (Get-Command node).Source
    $viteScript = Join-Path $projectRoot 'ui\node_modules\vite\bin\vite.js'
    $frontendProcess = Start-Process -FilePath $nodeExe -ArgumentList @(('"{0}"' -f $viteScript),'--host','127.0.0.1') -WorkingDirectory (Join-Path $projectRoot 'ui') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeRoot 'ui.out.log') -RedirectStandardError (Join-Path $runtimeRoot 'ui.err.log') -PassThru
    $frontendProcess.Id | Set-Content -LiteralPath (Join-Path $runtimeRoot 'ui.pid')
}
for ($attempt = 0; $attempt -lt 120; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 2
        $null = Invoke-WebRequest -Uri 'http://127.0.0.1:1422' -TimeoutSec 2 -UseBasicParsing
        if ($health.name -eq 'PICORA-2D') { Start-Process 'http://127.0.0.1:1422'; exit }
    } catch { }
    Start-Sleep -Milliseconds 500
}
throw '起動に失敗しました。.runtime内のログを確認してください。'
