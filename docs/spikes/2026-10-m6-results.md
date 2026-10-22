# M6 results

M6 wraps the PyInstaller folder `dist/Tamra` in a per-user Inno Setup 6 installer,
`Tamra-Setup-<version>.exe`, and adds `.github/workflows/release.yml`, which builds it and
proves it on a Windows runner. The exit criterion (spec §13) is "tagging a version publishes a
working installer". The version stays 0.1.0, and no tag is pushed in M6: the owner decides when
to push `v0.1.0`.

## What was built

- `packaging/tamra.iss`:
  - a per-user install with no administrator rights, into `%LOCALAPPDATA%\Programs\Tamra`,
    x64 only, Windows 10 1809 or later;
  - a fixed `AppId`, and the Restart Manager closes a running Tamra before an upgrade replaces
    its files;
  - a Start menu shortcut, an optional desktop icon, and "Launch Tamra" at the end;
  - the WebView2 check;
  - an uninstaller that removes only the install folder.
- `scripts/build.ps1 -Installer`: after the usual build it reads `tamra.__version__`, finds
  ISCC (on PATH, in `Program Files (x86)\Inno Setup 6`, or in
  `%LOCALAPPDATA%\Programs\Inno Setup 6`) and writes `dist/Tamra-Setup-<version>.exe`. Without
  the switch the build is unchanged.
- `tests/test_version.py`: `pyproject.toml`'s version equals `tamra.__version__`.
- `.github/workflows/release.yml`:
  - **Steps:** build, silent install into the runner's temp folder, the installed exe's
    `selfcheck`, silent uninstall (the installed `Tamra.exe` must be gone), then the sha256.
  - **Publishing:** PR and manual runs upload the installer and its sha256 as the
    `Tamra-Setup` artifact. A `v*` tag run checks that the tag is `v<tamra.__version__>`, and
    a second job, the only one with `contents: write`, creates the GitHub release.

## Local checks

The dev machine has no Inno Setup, so the installer itself is checked only by the workflow.

- `./scripts/build.ps1` without the switch built `dist/Tamra` as before, in 129 s including
  `npm ci`, and the packaged selfcheck exited 0 with `"ok": true`.
- `./scripts/build.ps1 -Installer` built the same folder, then stopped with exit code 1 and a
  message saying where it looked for ISCC.
- The workflow's YAML parses, and every `run` block parses as PowerShell. The sha256 step was
  run on a stand-in file. Its output has the `sha256sum` format.
- **Tests:** `uv run pytest` gives 897 passed (21 `assets` tests deselected). Ruff check and
  format are clean. `ui/` is unchanged.

## Workflow run on the PR

The PR's run of `release.yml` (pull request #9, 2026-10-22) passed every step:

| | Result |
|---|---|
| Run | https://github.com/EARTH157/tamra/actions/runs/39709592395 |
| Inno Setup on the runner | preinstalled, 6.7.1 (the Chocolatey `ISCC.exe` shim); choco not needed |
| Build (UI, PyInstaller, ISCC) | 111 s, of which ISCC 48 s |
| Silent install | exit 0, 8 s |
| Installed exe's selfcheck | exit 0, `ok: true` |
| Silent uninstall, `Tamra.exe` gone | exit 0, gone within 1 s |
| Installer size | 66,770,487 bytes (63.7 MB) |
| Installer sha256 | `bec22c936d5d0f50b09c3f6072e58a9164ed8de04ec4fa27de40591d38615cd3` |

## Not done in M6

- No code signing: SmartScreen warns on first run.
- No auto-update.
- No "delete my data" option in the uninstaller.
- The WebView2 Runtime is not bundled or downloaded; the installer only checks for it.
- The installer has not been tested on a clean Windows 10 VM, only on the GitHub runner.
- ARM64 Windows is not supported (x64 only).
