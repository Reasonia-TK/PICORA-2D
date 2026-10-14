$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache\uv'
Push-Location (Join-Path $projectRoot 'backend')
try {
    uv sync --locked --extra gpu --group dist
    if ($LASTEXITCODE -ne 0) { throw 'Python依存関係の同期に失敗しました。' }
} finally { Pop-Location }
Push-Location (Join-Path $projectRoot 'ui')
try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'UI依存関係の同期に失敗しました。' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'UIビルドに失敗しました。' }
} finally { Pop-Location }
