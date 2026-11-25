# AutoCutPR panel install: enable unsigned CEP panels + link the panel into
# Premiere's extensions folder. Run once; re-run safe.
$ErrorActionPreference = "Stop"

foreach ($v in 11, 12) {
    New-Item -Path "HKCU:\Software\Adobe\CSXS.$v" -Force | Out-Null
    Set-ItemProperty -Path "HKCU:\Software\Adobe\CSXS.$v" -Name PlayerDebugMode -Value "1" -Type String
}
Write-Host "PlayerDebugMode enabled for CSXS 11/12"

$dest = "$env:APPDATA\Adobe\CEP\extensions\AutoCutPR"
$src = Join-Path $PSScriptRoot "panel"
$configPath = Join-Path $src "config.json"
if (-not (Test-Path $configPath)) {
    $config = @{ engineDir = (Join-Path $PSScriptRoot "engine"); cacheDir = (Join-Path $PSScriptRoot "cache"); python = "python" } | ConvertTo-Json
    [IO.File]::WriteAllText($configPath, $config, (New-Object System.Text.UTF8Encoding($false)))
}
New-Item -ItemType Directory -Force "$env:APPDATA\Adobe\CEP\extensions" | Out-Null
if (Test-Path $dest) { Remove-Item $dest -Force -Recurse -Confirm:$false }
New-Item -ItemType Junction -Path $dest -Target $src | Out-Null
Write-Host "Panel linked: $dest -> $src"
Write-Host "Restart Premiere, then: Window > Extensions > AutoCutPR"
