# Tamra M6: installer and release workflow — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tagging a version publishes a working installer (spec §11, §13 M6).

**Architecture:**
- The existing `scripts/build.ps1` (UI + PyInstaller onedir) stays.
- An Inno Setup 6 script wraps `dist/Tamra` into a per-user installer.
- A new GitHub Actions workflow builds the installer and proves it works: silent install, the
  installed exe's selfcheck, silent uninstall.
  - On a pull request that touches packaging, it uploads the installer as an artifact.
  - On a `v*` tag, it attaches the installer to a GitHub release.

**Tech stack:**
- Inno Setup 6 (build tool only; nothing of it ships except the installer stub, which its
  license allows);
- GitHub Actions on `windows-latest`;
- PowerShell; pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-tamra-design.md` §11, §13.

## Global constraints

- Windows 10 1809+ / 11 x64. Python 3.12 via uv. Never run `uv python install`, never recreate
  `.venv`.
- No new Python or npm dependency. Every dependency stays Apache-2.0-compatible.
- No code signing and no auto-update in v1 (spec §11).
- **Never publish from a local machine.** No `git tag` push and no `gh release` from a
  developer run. Only the workflow publishes, and only for a tag the owner pushes.
- **User data:** `%LOCALAPPDATA%\Tamra\` is never deleted by the installer or the uninstaller.
  User document folders are never touched.
- Before every commit:
  - `uv run ruff format`, `uv run ruff check --fix`, `uv run ruff format --check`, `uv run pytest`;
  - if `ui/` is touched, also `npm --prefix ui test` and `npm --prefix ui run build`.
- Stage explicit paths only. Never commit `AGENTS.md` or `.claude/`. Never kill processes by
  image name.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Controller decisions (fixed)

1. **Per-user install, no admin:**
   - `PrivilegesRequired=lowest`;
   - `DefaultDirName={localappdata}\Programs\Tamra`;
   - x64 only (`ArchitecturesAllowed=x64compatible`, `ArchitecturesInstallIn64BitMode=x64compatible`);
   - `MinVersion=10.0.17763`.
   - Start-menu shortcut, plus an optional desktop icon (unchecked task).
   - Launch Tamra at the end (`postinstall`, `nowait`, `skipifsilent`).
2. **Fixed `AppId`:** a GUID generated once and written in the script, so upgrades replace the
   same install. `CloseApplications=yes` so the Restart Manager closes a running Tamra (and its
   child `llama-server.exe`) before files are replaced.
3. **Uninstall keeps the data:** documents, index, chats, models and settings stay under
   `%LOCALAPPDATA%\Tamra\`.
   - The uninstaller removes only the install folder.
   - The README says where the data is and how to remove it by hand.
   - No "delete my data" checkbox in v1.
4. **WebView2:** the installer checks the Evergreen runtime registry keys, per-machine and
   per-user (`{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`, `pv` value not empty and not `0.0.0.0`).
   - If it is missing: a message says Tamra needs the Microsoft Edge WebView2 Runtime and
     offers to open https://developer.microsoft.com/microsoft-edge/webview2/.
   - The install still proceeds.
   - Nothing is downloaded or bundled.
5. **One version:** `pyproject.toml` `version` and `tamra.__version__` must be equal (a test).
   - The installer version comes from `tamra.__version__` via `/DAppVersion=...`.
   - The output is `dist/Tamra-Setup-<version>.exe`.
   - The release workflow fails if the tag is not `v<__version__>`.
6. **Local build:** `scripts/build.ps1 -Installer` runs ISCC after PyInstaller. It finds ISCC:
   - on PATH,
   - in `Program Files (x86)\Inno Setup 6`,
   - in `%LOCALAPPDATA%\Programs\Inno Setup 6`.
   If none is found, it exits with a clear message. Without `-Installer` the build is unchanged.
7. **Workflow** `.github/workflows/release.yml`, separate from `ci.yml`:
   - **Triggers:**
     - `push: tags: ['v*']`;
     - `pull_request` with `paths` covering `packaging/**`, `scripts/build.ps1` and the
       workflow file itself;
     - `workflow_dispatch`.
   - **Inno Setup on the runner:** use ISCC if the image has it. Otherwise install it with
     `choco install innosetup --version=<pinned 6.x> -y --no-progress`.
   - **Steps:**
     1. Build: `./scripts/build.ps1 -Installer`.
     2. Silent install: `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /DIR=<temp dir>`,
        then wait for the process.
     3. Run the installed `Tamra.exe selfcheck --report r.json` with `Start-Process -Wait`.
        It must exit 0, and the report must be printed.
     4. Silent uninstall (`unins000.exe /VERYSILENT /SUPPRESSMSGBOXES`). The install folder's
        `Tamra.exe` must be gone afterwards.
     5. Write `Tamra-Setup-<version>.exe.sha256`.
   - **Publishing:**
     - PR and dispatch runs upload the installer and sha256 as an artifact.
     - Tag runs check `tag == "v" + __version__`, then `gh release create <tag>` with the
       installer, the sha256 and `--generate-notes`.
   - **Permissions:** `contents: read` by default; `contents: write` only on the job that
     creates the release, and only for tags.
   - **Pins:** the actions are pinned to major tags like `ci.yml` (`actions/checkout@v5` and so
     on).
8. **No version bump and no tag in M6.** The version stays 0.1.0. The owner decides when to
   push `v0.1.0`.

## Tasks

### Task 1: one version, with a test

- Files: `tests/test_version.py`, plus `src/tamra/__init__.py` only if needed.
- Test: read `pyproject.toml` with `tomllib` and assert `project.version == tamra.__version__`.
- Commit: `test: the package version matches pyproject`.

### Task 2: Inno Setup script and `build.ps1 -Installer`

- Files: create `packaging/tamra.iss`; modify `scripts/build.ps1`.
- `tamra.iss`:
  - `#ifndef AppVersion` gives an error;
  - `[Setup]` follows decisions 1–3;
  - `SetupIconFile` only if the repo has an `.ico`; check `packaging/` and the PyInstaller
    spec, and do not invent one;
  - `LicenseFile=..\LICENSE`;
  - `OutputDir=..\dist`, `OutputBaseFilename=Tamra-Setup-{#AppVersion}`;
  - `[Files]` takes `..\dist\Tamra\*` recursively;
  - `[Icons]` and `[Run]` as in decision 1;
  - a `[Code]` WebView2 check (decision 4) in `InitializeSetup` or `CurPageChanged`. It must
    not block silent installs: under `WizardSilent` it only logs.
- `build.ps1`: a `param([switch]$Installer)`.
  - It reads the version with `uv run python -c "import tamra; print(tamra.__version__)"`.
  - It finds ISCC (decision 6) and runs `ISCC /DAppVersion=<v> packaging\tamra.iss`, checking
    `$LASTEXITCODE`.
- Local check: ISCC is not on the controller's machine. Run `./scripts/build.ps1` without the
  switch to show nothing else changed, and say that the installer itself is verified by
  Task 3's workflow on the PR.
- Commit: `feat: Inno Setup installer script and build -Installer`.

### Task 3: release workflow

- Files: create `.github/workflows/release.yml`, following decision 7 exactly.
- **Shell:** all steps run in pwsh.
- **Waiting for the installer:** use `Start-Process -Wait -PassThru` and check `ExitCode`.
- **Logs:** pass `/LOG=install.log` to the installer and print the log if it fails.
- **Validation:** check the YAML locally, for example by parsing it with Python `yaml` if it is
  available. Do not add a dependency. If it is not available, read it carefully.
- Commit: `ci: build, install, selfcheck and uninstall the installer; release on tags`.

### Task 4: docs

- **README:**
  - install from the Releases page;
  - the SmartScreen warning ("More info → Run anyway", because v1 is unsigned);
  - the per-user install folder;
  - where the data lives and that uninstall keeps it;
  - WebView2.
- **Spec §11:** describe what was built:
  - per-user install;
  - the data kept on uninstall;
  - the WebView2 check;
  - the workflow's install, selfcheck and uninstall proof.
- **`docs/spikes/2026-10-m6-results.md`:**
  - the workflow run results, filled in after the PR's run;
  - installer size and build time;
  - a "Not done in M6" list: no signing, no auto-update, no delete-data option, WebView2 not
    bundled, the installer never tested on a clean Windows 10 VM, ARM64 not supported.
- Commit: `docs: M6 installer, release workflow and results`.

### Task 5 (controller): PR and proof

- Push `m6-release`, open the PR, and read the release workflow's run on the PR: install,
  selfcheck and uninstall. Download the artifact and record its size and sha256 in the results
  doc.
- Merge when CI and the workflow are green.
- Ask the owner before pushing any tag.
