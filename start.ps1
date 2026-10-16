param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$ProjectPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $ProjectPython)) {
    & (Join-Path $PSScriptRoot 'setup.ps1')
}
& $ProjectPython -c 'import flask, pypdf, jwt' 2>$null
if ($LASTEXITCODE -ne 0) { & (Join-Path $PSScriptRoot 'setup.ps1') }
$LaunchArguments = @('run.py', '--port', "$Port")
if (-not $NoBrowser) { $LaunchArguments += '--open-browser' }
& $ProjectPython @LaunchArguments
exit $LASTEXITCODE
