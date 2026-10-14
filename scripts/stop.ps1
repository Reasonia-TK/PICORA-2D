# start.ps1 -Browserで起動した、この作業フォルダーのプロセスだけを止める。
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$runtimeRoot = Join-Path $projectRoot '.runtime'
function Stop-OwnedProcessTree([int]$processId) {
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $processId")
    foreach ($child in $children) { Stop-OwnedProcessTree -processId $child.ProcessId }
    Stop-Process -Id $processId -ErrorAction SilentlyContinue
}
foreach ($name in @('ui', 'backend')) {
    $pidFile = Join-Path $runtimeRoot "$name.pid"
    if (-not (Test-Path -LiteralPath $pidFile)) { continue }
    $pidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($pidText -notmatch '^\d+$') { throw "プロセス番号を読めません: $pidFile" }
    $ownedProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $pidText"
    if ($ownedProcess -and $ownedProcess.CommandLine -and $ownedProcess.CommandLine.IndexOf($projectRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        Stop-OwnedProcessTree -processId ([int]$pidText)
    }
    Remove-Item -LiteralPath $pidFile
}
