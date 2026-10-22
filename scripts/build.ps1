# Builds dist\Tamra\Tamra.exe; with -Installer, also dist\Tamra-Setup-<version>.exe (Inno Setup 6).
param([switch]$Installer)
# Native tools write progress to stderr; each step's exit code is checked below.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
npm --prefix ui ci
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm --prefix ui run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run python scripts/fetch_assets.py llama
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run pyinstaller packaging/tamra.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not $Installer) { exit 0 }

$version = uv run python -c "import tamra; print(tamra.__version__)"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    Write-Host "ISCC.exe (Inno Setup 6) was not found on PATH, in Program Files (x86)\Inno Setup 6," `
        "or in %LOCALAPPDATA%\Programs\Inno Setup 6. Install Inno Setup 6, or build without -Installer."
    exit 1
}
& $iscc "/DAppVersion=$version" packaging\tamra.iss
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
