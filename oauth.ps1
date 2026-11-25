# One-time setup and explicit management for AutoCutPR's local OAuth proxy.
param(
    [ValidateSet('setup', 'login', 'start', 'status', 'stop')]
    [string]$Action = 'setup'
)
$ErrorActionPreference = 'Stop'
$nodePath = (Get-Command node -ErrorAction Stop).Source
$major = [int]((& $nodePath --version).TrimStart('v').Split('.')[0])
if ($major -lt 20) { throw 'Install Node.js 20 or newer, then retry.' }
$oauthDir = Join-Path $PSScriptRoot 'oauth'
$cliPath = Join-Path $oauthDir 'node_modules/openai-oauth/dist/cli.js'
$previousRuntimeDir = $env:OPENAI_OAUTH_INTERNAL_RUNTIME_DIR
$env:OPENAI_OAUTH_INTERNAL_RUNTIME_DIR = Join-Path $PSScriptRoot 'cache/oauth'
try {
    if ($Action -eq 'setup') {
        Push-Location $oauthDir
        try {
            & npm.cmd ci --ignore-scripts --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'OAuth dependency installation failed.' }
        } finally { Pop-Location }
    }
    if (!(Test-Path -LiteralPath $cliPath)) { throw 'Run .\oauth.ps1 setup first.' }
    if ($Action -eq 'setup') {
        & $nodePath (Join-Path $oauthDir 'check-login.mjs')
        if ($LASTEXITCODE -eq 2) {
            & $nodePath $cliPath login
            if ($LASTEXITCODE -ne 0) { throw 'ChatGPT login did not complete.' }
        } elseif ($LASTEXITCODE -ne 0) {
            throw 'Could not verify ChatGPT login. Check your connection and retry.'
        }
        Write-Host 'Setup complete. AutoCutPR starts the OAuth proxy when an AI feature needs it.'
        Write-Host 'GPT-6 Astra account access will be checked before each AI operation.'
    } elseif ($Action -eq 'start') {
        & $nodePath $cliPath --detach --host 127.0.0.1 --port 10531
        if ($LASTEXITCODE -ne 0) { throw 'OAuth startup failed.' }
    } else {
        & $nodePath $cliPath $Action
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
} finally {
    $env:OPENAI_OAUTH_INTERNAL_RUNTIME_DIR = $previousRuntimeDir
}
