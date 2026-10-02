$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
npm --prefix ui ci
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
npm --prefix ui run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run python scripts/fetch_assets.py llama
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run pyinstaller packaging/tamra.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
