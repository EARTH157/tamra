#!/usr/bin/env bash
# Builds dist/tamra/tamra; with --deb, also dist/tamra_<version>_amd64.deb.
# The Linux version of scripts/build.ps1 [-Installer]. Run on Ubuntu 24.04.
set -euo pipefail
cd "$(dirname "$0")/.."
deb=0
case "${1:-}" in
  --deb) deb=1 ;;
  "") ;;
  *) echo "usage: scripts/build.sh [--deb]" >&2; exit 2 ;;
esac
npm --prefix ui ci
npm --prefix ui run build
uv run python scripts/fetch_assets.py llama
uv run pyinstaller packaging/tamra.spec --noconfirm --distpath dist --workpath build
if [ "$deb" = 1 ]; then
  uv run python scripts/make_deb.py
fi
