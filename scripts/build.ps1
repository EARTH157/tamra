$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
npm --prefix ui ci
npm --prefix ui run build
uv run python scripts/fetch_assets.py llama
uv run pyinstaller packaging/tamra.spec --noconfirm --distpath dist --workpath build
