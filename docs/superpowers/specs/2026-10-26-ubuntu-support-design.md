# Ubuntu support: Tamra as a desktop app on Ubuntu 24.04 and later

Status: approved in brainstorming (2026-10-26), pending plan.
Date: 2026-10-26.

## Goal

Tamra runs only on Windows today. Ubuntu users get the same desktop app: a window, a `.deb`
file to install with `apt`, a menu entry, and the same features (local and cloud models,
citations, the source panel and viewer, OCR, projects, highlights, MCP). The Windows app keeps
working exactly as it does now.

The port stays in Python. The work is in about ten places that call Windows APIs directly
(job objects, a named mutex, `os.startfile`, `%LOCALAPPDATA%`, ...), plus packaging and CI. A
rewrite in another language would face the same per-OS places and would not speed up answers,
because the heavy work already runs in native code (llama.cpp, ONNX Runtime, pdfium, SQLite,
tokenizers).

### Decisions

| Decision | Choice | Reason |
|---|---|---|
| Ubuntu releases | 24.04 LTS and later, x86_64 | Built on 24.04, the bundle runs on 24.04, 25.x and 26.04 (glibc is forward-compatible). One release to test. |
| Window engine | pywebview's Qt backend with PySide6 (Qt WebEngine) | Chromium, the same engine as WebView2. Text selection (Check source), highlight marks and PDF pages behave as on Windows. PySide6 installs from wheels, so `uv sync` needs no compiler. |
| Package | `tamra_<version>_amd64.deb` | `apt install ./tamra_….deb` pulls the system libraries, adds a menu entry and uninstalls with `apt remove`, like `Tamra-Setup.exe` on Windows. |
| Local LLM | llama.cpp `b11344`, `llama-b11344-bin-ubuntu-vulkan-x64.tar.gz` | The same llama.cpp build as Windows, and the same Vulkan + CPU fallback. Mesa and the NVIDIA driver both provide Vulkan. |
| Data folder | `$XDG_DATA_HOME/tamra`, default `~/.local/share/tamra` (override: `TAMRA_DATA_DIR`) | The XDG standard location. |
| API keys | `keyring`'s Secret Service backend (GNOME Keyring on Ubuntu) | `keyring` already selects it on Linux. Its `secretstorage` and `jeepney` dependencies are MIT/BSD. |

**Licenses.** PySide6 and Qt are LGPLv3 and Qt WebEngine bundles Chromium (BSD-style). These
libraries are linked dynamically in the PyInstaller one-folder bundle, so a user can replace
them, which LGPL requires. Never PyQt6: it is GPL. The rule "no AGPL or GPL" is unchanged.

**Out of scope (YAGNI):** other distributions (they may work but are not tested), arm64,
AppImage, Flatpak and Snap, an apt repository or package signing, auto-update, CUDA or ROCm
builds of llama.cpp, macOS, and a headless or server mode.

## 1. Platform layer

Each OS difference is handled in the module that already owns it, by checking `sys.platform`.
There is no new abstraction layer. The Windows branches keep their current code.

### 1.1 Data folder and permissions (`paths.py`)

- Linux: `TAMRA_DATA_DIR` if set, else `$XDG_DATA_HOME/tamra` when `XDG_DATA_HOME` is an
  absolute path, else `~/.local/share/tamra`.
- On Windows the per-user AppData folder is private. On Linux a new folder is `0755` under the
  usual umask, so other local users could read the chats, the index and the MCP token. So on
  POSIX `data_dir()` makes the folder `0700`: it creates it with that mode, and it sets the
  mode of the default folder on every call. A folder the user set with `TAMRA_DATA_DIR` keeps
  its mode if it already exists.
- `discovery.write_discovery` creates its temporary file with mode `0600` (`os.open`) before
  the atomic replace. The data folder already protects it, and this keeps the token private
  even inside a `TAMRA_DATA_DIR` that others can read.
- `paths.llama_server()` returns `resource_dir()/vendor/llama/llama-server.exe` on Windows and
  `.../llama-server` on Linux. It replaces the three hard-coded paths in `app.build_core`,
  `__main__` (selfcheck) and the tests' asset fixture.

### 1.2 Child processes (`winjob.py` becomes `childproc.py`)

`childproc.spawn(args, **popen_kwargs) -> (Popen, job)` starts a process that ends when Tamra
ends, even when Tamra crashes or is killed. `job` is the Windows job to close when the process
is stopped, and `None` on Linux.

- **Windows:** the current kill-on-close Job Object, unchanged. If Windows refuses the job,
  the process runs without it and a warning is logged, as now.
- **Linux:** `prctl(PR_SET_PDEATHSIG, SIGKILL)` in the child, through libc. Linux sends this
  signal when the *thread* that started the child exits, not the process. llama-server is
  started from request threads, and anyio closes idle worker threads after about 10 seconds,
  which would kill llama-server. So every `spawn` call runs `Popen` on one dedicated daemon
  thread that `childproc` owns and that lives as long as Tamra. The child also checks
  `getppid()` after `prctl`, so that it exits if Tamra died before the signal was set.

`childproc.NO_WINDOW` is `subprocess.CREATE_NO_WINDOW` on Windows and `0` on Linux. The
`creationflags` in `llama_server.py` and `hardware.detect` use it, because the Windows
constant does not exist on Linux.

### 1.3 One Tamra per data folder (`app.py`)

- **Windows:** the named mutex, unchanged.
- **Linux:** an exclusive `fcntl.flock` (`LOCK_EX | LOCK_NB`) on `<data_dir>/tamra.lock`
  (mode `0600`), held for the process lifetime. The kernel releases it when Tamra exits or
  crashes. The lock file is in the data folder, so a run with its own `TAMRA_DATA_DIR` never
  collides. A second launch shows "Tamra is already running.", as on Windows.

### 1.4 Opening files and the data folder (`app.py`)

On Linux `open_data_folder` and `open_external` run `xdg-open <path>`: no shell, `stdin`,
`stdout` and `stderr` set to `/dev/null`, in a new session so the opened app outlives Tamra.
As now, the path comes only from `data_dir()` or from the store, never from a request.

### 1.5 The error dialog (`app.py`)

The windowed app has no console. On Linux `_alert` runs
`zenity --error --no-markup --title=Tamra --text=<message>` when `zenity` is on `PATH` (the
`.deb` recommends it, and it is part of Ubuntu Desktop). Otherwise it writes the message to
stderr. The message is logged in either case.

### 1.6 RAM (`models/hardware.py`)

On Linux `_ram_gb` is `os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")`. On Windows
it still uses `GlobalMemoryStatusEx`.

### 1.7 Integrated GPUs (`models/hardware.py`)

The tier advice skips integrated GPUs. Today it recognizes them by their Windows driver names
("Radeon(TM) … Graphics", "Intel … Arc(TM) A770"). Mesa's Vulkan drivers use other names
("AMD Radeon 780M (RADV PHOENIX)", "Intel(R) Arc(tm) A770 Graphics (DG2)"), so on Linux an AMD
iGPU would count as a discrete GPU. llama.cpp prints each Vulkan device's memory type in
`--list-devices` (`ggml_vulkan: 0 = … | uma: 1 | …`). `parse_devices` reads that flag into a
new `Gpu.integrated: bool | None`. `_is_integrated` uses the flag when llama.cpp printed it,
and the current name rules when it did not. This applies on both platforms: on Windows the
flag agrees with the name rules for every GPU in the tests.

### 1.8 Removable drives (`drives.py`)

On Linux a path counts as removable when its resolved absolute path is under `/media/` or
`/run/media/`. That is where Ubuntu (udisks2) mounts USB drives and discs. So a model file
picked from a USB stick is copied, as on Windows, instead of linked.

### 1.9 Upper and lower case in paths

Windows ignores case in paths and Linux does not. `os.path.normcase` already handles this in
`core._overlaps`, `core._path_key` and `models/external.py`. Two places ignore case
everywhere: the indexer's excluded-folder check (`ingest/indexer.py:_under`) and the mutex
name. The first now ignores case only on Windows. The mutex is Windows-only (§1.3).

### 1.10 Ollama installed as a service

Ollama's Linux installer runs it as the `ollama` user and keeps models in
`/usr/share/ollama/.ollama/models`. On Linux, when `OLLAMA_MODELS` is not set, the external
model search also looks there, after `~/.ollama/models`. A folder Tamra cannot read is
skipped, like any missing folder today.

### 1.11 API keys and platform wording

- `secrets.py` is unchanged. Its docstring names both stores.
- `GET /api/settings` gains a read-only `"platform": "windows" | "linux"`.
- The UI strings that name Windows depend on it. On Linux, `settings.apiKeyNote` and the
  delete confirmation name "GNOME Keyring" (in English and Thai). On Windows they keep "Windows
  Credential Manager". `settings.theme.system` becomes "Match system" / "ตามระบบ" on both
  platforms.
- If no Secret Service is running, saving a key fails with the existing error, and selfcheck
  reports the backend it found, as it does now.

## 2. The window

- `pyproject.toml` adds `pywebview[pyside6]` with the marker `sys_platform == 'linux'`.
  Windows keeps the plain `pywebview` dependency and WebView2.
- On Linux `_show_window` sets `QT_API=pyside6`. Before `webview.start(gui="qt")` it creates
  the `QApplication` itself and calls `setDesktopFileName("tamra")`. pywebview reuses an
  existing `QApplication`. This makes GNOME match the window to `tamra.desktop` and show
  Tamra's icon in the dock on both Wayland and X11.
- The window icon is `packaging/tamra.png` (256 px). `scripts/make_icon.py` renders it from
  `ui/src/logo.svg` beside `tamra.ico`.
- The folder and model-file pickers use Qt's file dialog through pywebview's
  `create_file_dialog`. `FolderPicker` and `FilePicker` are unchanged.
- **Chromium sandbox.** Ubuntu 23.10 and later block unprivileged user namespaces through
  AppArmor (`kernel.apparmor_restrict_unprivileged_userns=1`). Chromium's sandbox needs them,
  so without an AppArmor profile Qt WebEngine stops with "No usable sandbox". The `.deb`
  installs `/etc/apparmor.d/tamra`, which grants `userns` to `/opt/tamra/tamra` and to its
  `QtWebEngineProcess`, as Chrome's and VS Code's packages do. The sandbox stays on. A run
  from source has no profile. The README says to start it with `QTWEBENGINE_DISABLE_SANDBOX=1`
  on such systems. Tamra never turns the sandbox off by itself.
- **Fonts.** Thai and Chinese text needs fonts the system provides. The `.deb` recommends
  `fonts-thai-tlwg` and `fonts-noto-cjk`. Ubuntu Desktop installs both by default.
- **`tamra selfcheck --window`** (new, both platforms). It opens a real window on the bundled
  `ui/dist` through pywebview's local HTTP server and waits up to 30 s for the page to load and
  for `document.getElementById("root").childElementCount > 0`. Then it closes the window and
  adds `"window": {"ok": …, "detail": …}` to the report. This shows that the web engine, its
  sandbox and the bundled UI work, which the current selfcheck cannot show. CI runs it under
  `xvfb-run`. Without `--window`, selfcheck opens no window, as now.

## 3. Assets

- `scripts/assets.json` gains `llama-linux`: the URL of
  `llama-b11344-bin-ubuntu-vulkan-x64.tar.gz`,
  sha256 `b8adfbb4eff636fcefb9574f52d8832d4ea7e9e3afbb8659d8ea538bad462b7a` (31.5 MB), and
  `extract_to: vendor/llama`. The Windows entry becomes `llama-windows`.
- `fetch_assets.py llama` fetches `llama-windows` or `llama-linux` for the current platform.
  This keeps `build.ps1` and the docs unchanged. With no names, it fetches every asset that is
  not for the other platform.
- `.tar.gz` assets are extracted with `tarfile`'s `data` filter. It keeps the executable bits
  and the symlinks of the `.so` files, and it refuses absolute paths and paths outside the
  target folder. If the archive puts everything under one top-level folder, its contents are
  moved up, so `vendor/llama/llama-server` exists, as `vendor/llama/llama-server.exe` does on
  Windows.
- llama-server finds its `libggml*.so` and `libllama.so` next to itself (RPATH `$ORIGIN`,
  checked in the plan). It needs only `libvulkan1` and the C/C++ runtime from the system.

## 4. Packaging

### 4.1 The PyInstaller bundle (`packaging/tamra.spec`)

The spec branches on the platform:

- Windows: unchanged (`Tamra.exe`, `tamra.ico`, the Windows keyring backend).
- Linux: the executable is `tamra`. The data files include `tamra.png` instead of `tamra.ico`.
  The hidden imports are `keyring.backends.SecretService`, `secretstorage` and `jeepney`, and
  PyInstaller's PySide6 hooks collect Qt WebEngine (`QtWebEngineProcess`, resources and
  locales). Qt modules Tamra does not import are excluded, to keep the bundle small.

### 4.2 `scripts/build.sh [--deb]`

This is the Linux version of `build.ps1 [-Installer]`. It runs `npm ci`, the UI build,
`fetch_assets.py llama` and PyInstaller, and produces `dist/tamra/tamra`. With `--deb` it also
lays out `build/deb/` and runs `dpkg-deb --build --root-owner-group` to produce
`dist/tamra_<version>_amd64.deb`. The version is `tamra.__version__`.

### 4.3 What the `.deb` contains

| Path | What |
|---|---|
| `/opt/tamra/` | The PyInstaller folder (`tamra`, `_internal/`) |
| `/usr/bin/tamra` | A symlink to `/opt/tamra/tamra` |
| `/usr/share/applications/tamra.desktop` | `Name=Tamra`, `Exec=tamra`, `Icon=tamra`, `StartupWMClass=tamra`, `Categories=Office;`, `Terminal=false` |
| `/usr/share/icons/hicolor/scalable/apps/tamra.svg`, `…/256x256/apps/tamra.png` | The icon |
| `/etc/apparmor.d/tamra` | The `userns` profile (§2) |
| `/usr/share/doc/tamra/copyright` | Apache-2.0, `NOTICE`, and the LGPLv3 notice for Qt/PySide6 |

`DEBIAN/control` declares `Architecture: amd64`. `Depends` lists the system libraries that the
bundle and llama-server load. The plan fixes this list by running `ldd` on every bundled ELF
file on a clean `ubuntu:24.04`. It includes `libvulkan1`, `libnss3`, `libxkbcommon-x11-0`,
`libxcb-cursor0`, `libegl1`, `libfontconfig1`, `libasound2t64` and `ca-certificates`.
`Recommends` lists `gnome-keyring`, `zenity`, `mesa-vulkan-drivers`, `fonts-thai-tlwg` and
`fonts-noto-cjk`.

`postinst` loads the AppArmor profile (`apparmor_parser -r`) when AppArmor is enabled.
`postrm` unloads it on `remove` and `purge`. dpkg's triggers refresh the menu and the icon
cache.

**Uninstall keeps the data.** As on Windows, `apt remove` and `apt purge` remove only what the
package installed. `~/.local/share/tamra` stays. The README says how to delete it by hand.

**Upgrade.** Installing a newer `.deb` replaces the old one. A running Tamra keeps running from
its loaded files until the user restarts it. dpkg replaces files atomically.

## 5. CI and release

- **`ci.yml`, job `test`:** a matrix of `windows-latest` and `ubuntu-24.04`. Both run ruff,
  pytest, the Vitest tests and the UI build.
- **`ci.yml`, job `package`:** a matrix as well. The Windows leg is unchanged. The Ubuntu leg
  runs `scripts/build.sh --deb`, then `sudo apt-get install -y ./dist/tamra_*.deb`. It checks
  that `/opt/tamra/_internal/vendor/llama/llama-server` is executable, then runs
  `tamra selfcheck --report` and `xvfb-run -a tamra selfcheck --window`. The runner has the
  AppArmor userns restriction, so this also checks the profile. It uploads the `.deb`.
- **`release.yml`:** a new job `deb` on `ubuntu-24.04`: build, install, both selfchecks,
  `apt-get remove`, check that `/opt/tamra` is gone, and write
  `tamra_<version>_amd64.deb.sha256`. The tag check is the same as on Windows. The `release`
  job attaches the `.deb` and its sha256 beside the Windows installer.

## 6. Testing

- **pytest passes on both platforms.**
  - Tests of Windows-only code skip elsewhere and say why. These are the job object, the
    mutex, `GetDriveTypeW`, `FILE_ATTRIBUTE_HIDDEN`, NTFS junctions and the
    `CREATE_NO_WINDOW` assertion.
  - Tests of Linux-only code skip elsewhere. These are `PR_SET_PDEATHSIG` (llama-server dies
    when its parent is killed, including when the starting thread has ended), `flock`, the
    `0700`/`0600` modes, `xdg-open` (with a fake on `PATH`) and `sysconf`.
  - Pure logic runs everywhere: XDG path choice from the environment, `uma:` parsing,
    `/media` checks on given paths, and per-platform asset selection.
- **`scripts/exe_smoke.py`** works on both platforms. Its default executable is
  `dist/tamra/tamra` on Linux, and it lists llama-server processes with `pgrep -x llama-server`
  instead of `tasklist`.
- **Developer check in WSL.** Ubuntu-24.04 runs in WSL2 on the development machine. With WSLg
  it can show the window: the full pytest suite, `tamra --dev` with the browser UI, and the
  installed `.deb` with its window. WSL has no AppArmor userns restriction, so only CI and the
  manual check below test the sandbox profile.
- **Manual check on a real Ubuntu 24.04 desktop (GNOME, Wayland, AppArmor on)** before the
  first release with the `.deb`:
  - It installs with `apt`, and the menu entry opens the window with Tamra's icon in the dock.
  - The folder picker adds a project, and a PDF is indexed and answered with citations from a
    local model (GPU through Vulkan, or the CPU).
  - Check source opens the source panel, and opening the file uses the default app.
  - An API key is saved in GNOME Keyring and survives a restart.
  - A second launch says "already running".
  - Closing Tamra ends llama-server, and so does `kill -9` of Tamra.
  - `apt remove` keeps `~/.local/share/tamra`.

## 7. Documentation

- **README** (English and Thai): an "Install on Ubuntu" section (download the `.deb`,
  `sudo apt install ./tamra_<version>_amd64.deb`, where the data lives, how to uninstall and
  delete the data), and Ubuntu steps for running from source (the Qt runtime packages, and
  `QTWEBENGINE_DISABLE_SANDBOX=1`).
- **CLAUDE.md / AGENTS.md:** the data-folder rule names both platforms. The local-LLM rule
  says `llama-server` (`.exe` on Windows). The license rule says PySide6 (LGPLv3) is allowed
  and PyQt never. The command list gains `./scripts/build.sh --deb`.
- **Main spec (`2026-10-02-tamra-design.md`):** §1's platform non-goal becomes "platforms
  other than Windows 10/11 x64 and Ubuntu 24.04+ x86_64". The §2 Stack and Data location rows
  name both platforms. §11 points to this spec for Linux packaging.

## 8. Risks

| Risk | What we do |
|---|---|
| The `.deb` is large (Qt WebEngine is ~200 MB unpacked) | Exclude unused Qt modules. The plan measures the size. A `.deb` up to ~150 MB is acceptable next to models of 1–5 GB. |
| The sandbox fails on a desktop with a different AppArmor setup | The profile is checked in CI on an AppArmor runner and by the manual check. A failure shows in `selfcheck --window`. |
| A blank window in VMs or with unusual GPU drivers (Chromium's GPU process) | The README documents `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`. The answers do not depend on it, because llama-server uses the GPU separately. |
| No Vulkan driver | llama-server falls back to the CPU, as on Windows. `mesa-vulkan-drivers` is recommended. |
| No Secret Service (a minimal desktop or another environment) | Saving a key fails with the existing error. Local models and everything else still work. |
| Paths that differ only in case on Linux | `normcase` already does the right thing on each OS. §1.9 fixes the two places that ignore case everywhere. |
