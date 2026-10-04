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
