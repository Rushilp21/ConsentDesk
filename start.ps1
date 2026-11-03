$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Get-Command python -ErrorAction SilentlyContinue
if ($taskPython) {
    & $taskPython.Source server.py
} else {
    $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $bundledPython) {
        & $bundledPython server.py
    } else {
        throw 'Install Python 3.11 or newer, then run python server.py.'
    }
}
