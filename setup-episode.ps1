param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$episodeRoot = $PSScriptRoot
$episodeVenv = Join-Path $episodeRoot '.episode-venv'
& $Python -m venv $episodeVenv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated Full Episode environment.' }
$episodePython = Join-Path $episodeVenv 'Scripts/python.exe'
& $episodePython -m pip install -r (Join-Path $episodeRoot 'engine/episode-requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Full Episode dependency installation failed.' }
Write-Host 'Full Episode video tools are ready. The existing speech environment is unchanged.'
