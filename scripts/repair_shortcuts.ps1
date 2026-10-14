# Repair only shortcuts whose target is this installed PICORA-2D executable.
param(
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'PICORA-2D'),
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$installRoot = (Resolve-Path -LiteralPath $InstallDirectory).Path
$appExe = Join-Path $installRoot 'picora-2d.exe'
if (-not (Test-Path -LiteralPath $appExe)) { throw "PICORA-2D executable is missing: $appExe" }
if ((Get-Item -LiteralPath $appExe).VersionInfo.ProductName -ne 'PICORA-2D') { throw 'Unexpected application' }
$sourceIcon = Join-Path $projectRoot 'ui\src-tauri\icons\icon.ico'
$iconFile = Join-Path $installRoot 'icons\picora-shared-ad39f965.ico'
$expectedIcon = "$iconFile,0"
if (-not $CheckOnly) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $iconFile) | Out-Null
    Copy-Item -LiteralPath $sourceIcon -Destination $iconFile -Force
}
if (-not (Test-Path -LiteralPath $iconFile)) { throw "Dedicated icon is missing: $iconFile" }
if ((Get-FileHash -LiteralPath $sourceIcon).Hash -ne (Get-FileHash -LiteralPath $iconFile).Hash) { throw 'Icon does not match PICORA-1D' }

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PicoraShortcutNotify {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern void SHChangeNotify(int eventId, uint flags, string path, IntPtr other);
}
'@
$wsh = New-Object -ComObject WScript.Shell
$shell = New-Object -ComObject Shell.Application
$folders = @(
    [Environment]::GetFolderPath('Desktop'),
    [Environment]::GetFolderPath('StartMenu'),
    (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar')
) | Select-Object -Unique
$backupRoot = Join-Path $projectRoot ('.runtime\shortcut-backups\' + [Guid]::NewGuid().ToString('N'))
$report = @()
foreach ($folder in $folders) {
    if (-not (Test-Path -LiteralPath $folder)) { continue }
    foreach ($file in (Get-ChildItem -LiteralPath $folder -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue)) {
        $link = $wsh.CreateShortcut($file.FullName)
        if (-not [string]::Equals($link.TargetPath, $appExe, [StringComparison]::OrdinalIgnoreCase)) { continue }
        $item = $shell.Namespace($file.DirectoryName).ParseName($file.Name)
        $appId = $item.ExtendedProperty('System.AppUserModel.ID')
        $arguments = $link.Arguments
        $backup = $null
        if (-not $CheckOnly -and $link.IconLocation -ne $expectedIcon) {
            New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
            $backup = Join-Path $backupRoot ([Guid]::NewGuid().ToString('N') + '.lnk')
            Copy-Item -LiteralPath $file.FullName -Destination $backup
            $link.IconLocation = $expectedIcon
            $link.Save()
            [PicoraShortcutNotify]::SHChangeNotify(0x2000, 0x3005, $file.FullName, [IntPtr]::Zero)
        }
        $updated = $wsh.CreateShortcut($file.FullName)
        $updatedItem = $shell.Namespace($file.DirectoryName).ParseName($file.Name)
        if ($updated.Arguments -ne $arguments -or $updatedItem.ExtendedProperty('System.AppUserModel.ID') -ne $appId) {
            if ($backup) { Copy-Item -LiteralPath $backup -Destination $file.FullName -Force }
            throw 'Shortcut properties changed unexpectedly; backup restored'
        }
        $report += [PSCustomObject]@{ Link=$file.FullName; Target=$updated.TargetPath; Icon=$updated.IconLocation; AppUserModelId=$appId; Matches=($updated.IconLocation -eq $expectedIcon); Backup=$backup }
    }
}
if (-not $CheckOnly) { [PicoraShortcutNotify]::SHChangeNotify(0x2000, 0x3005, $iconFile, [IntPtr]::Zero) }
$report | ConvertTo-Json -Depth 3
if ($report.Where({ -not $_.Matches }).Count -gt 0) { throw 'A shortcut still has an unexpected icon' }
