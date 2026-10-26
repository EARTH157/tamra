"""Build dist/tamra_<version>_amd64.deb from the PyInstaller folder dist/tamra.

Run on Ubuntu 24.04 after PyInstaller: uv run python scripts/make_deb.py (scripts/build.sh --deb
runs both). Needs dpkg-deb, readelf and ldconfig, and the system libraries the bundle loads
(packaging/linux/build-packages.txt), so their packages can be named in Depends.
docs/superpowers/specs/2026-10-26-ubuntu-support-design.md §4.3 lists what the package holds.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "dist" / "tamra"
LINUX = ROOT / "packaging" / "linux"
STAGE = ROOT / "build" / "deb"
WEBENGINE_PROCESS = "_internal/PySide6/Qt/libexec/QtWebEngineProcess"  # named in apparmor-tamra

MAINTAINER = "EARTH157 <EARTH157@users.noreply.github.com>"
# Not found by scanning the binaries: certificates for truststore, xdg-open for "Open file".
EXTRA_DEPENDS = ["ca-certificates", "xdg-utils"]
RECOMMENDS = ["gnome-keyring", "zenity", "mesa-vulkan-drivers", "fonts-thai-tlwg", "fonts-noto-cjk"]
SUMMARY = "Ask questions about your own documents, with checkable citations"
DESCRIPTION = """\
 Tamra answers questions about your documents (PDF, DOCX, TXT, MD) in Thai,
 English and Chinese. Every answer cites its sources, and any sentence can be
 checked against the exact passage. It runs a local model through llama.cpp,
 or a cloud API, and works offline once the models are downloaded."""
# Distributions whose license files ship with the package (LGPL-3.0: Qt and PySide6).
LICENSED = ("PySide6_Essentials", "PySide6_Addons", "PySide6_WebEngine", "shiboken6")
_NEEDED = re.compile(r"\(NEEDED\)\s+Shared library: \[(.+?)\]")
_LDCONFIG = re.compile(r"^\s*(\S+) \(libc6,x86-64[^)]*\) => (\S+)$")


def control(version: str, depends: list[str], installed_kb: int) -> str:
    fields = [
        "Package: tamra",
        f"Version: {version}",
        "Section: utils",
        "Priority: optional",
        "Architecture: amd64",
        f"Maintainer: {MAINTAINER}",
        f"Installed-Size: {installed_kb}",
        f"Depends: {', '.join(depends)}",
        f"Recommends: {', '.join(RECOMMENDS)}",
        "Homepage: https://github.com/EARTH157/tamra",
        f"Description: {SUMMARY}",
        DESCRIPTION,
    ]
    return "\n".join(fields) + "\n"


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def _elf_files(folder: Path) -> list[Path]:
    """Every ELF file in the bundle: executables, .so files and Qt plugins."""
    found = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as f:
                if f.read(4) == b"\x7fELF":
                    found.append(path)
    return found


def depends(bundle: Path) -> list[str]:
    """The packages that provide every library the bundle needs and does not carry itself."""
    inside = {p.name for p in bundle.rglob("*") if ".so" in p.name}
    wanted: set[str] = set()
    for elf in _elf_files(bundle):
        wanted |= set(_NEEDED.findall(_run(["readelf", "-d", str(elf)])))
    wanted -= inside
    where: dict[str, Path] = {}
    for line in _run(["ldconfig", "-p"]).splitlines():
        m = _LDCONFIG.match(line)
        if m and m[1] in wanted:
            where.setdefault(m[1], Path(m[2]))
    missing = sorted(wanted - where.keys())
    if missing:
        raise SystemExit(
            "not on this system: " + ", ".join(missing) + ". Install their packages "
            "(packaging/linux/build-packages.txt) so Depends can name them."
        )
    return sorted({_package_of(path) for path in where.values()} | set(EXTRA_DEPENDS))


def _package_of(path: Path) -> str:
    """The package that owns a library file. With merged /usr, dpkg may know it under /lib or
    under /usr/lib, so both spellings are asked."""
    spellings = {path, path.resolve()}
    for p in list(spellings):
        s = str(p)
        spellings.add(Path(s[4:]) if s.startswith("/usr/") else Path("/usr" + s))
    for p in sorted(spellings):
        done = subprocess.run(["dpkg", "-S", str(p)], capture_output=True, text=True)
        owner = _owner(done.stdout) if done.returncode == 0 else None
        if owner:
            return owner
    raise SystemExit(f"no package owns {path}")


def _owner(dpkg_s: str) -> str | None:
    """The package in `dpkg -S` output ("libnss3:amd64: /usr/lib/…"), past any diversion lines."""
    for line in dpkg_s.splitlines():
        if ": " in line and not line.startswith("diversion "):
            return line.rsplit(": ", 1)[0].split(",")[0].strip().split(":")[0]
    return None


def _install(source: Path, dest: Path, mode: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    dest.chmod(mode)


def stage(version: str) -> None:
    if not (BUNDLE / WEBENGINE_PROCESS).is_file():
        raise SystemExit(f"missing {BUNDLE / WEBENGINE_PROCESS}; the AppArmor profile names it")
    shutil.rmtree(STAGE, ignore_errors=True)
    shutil.copytree(BUNDLE, STAGE / "opt" / "tamra", symlinks=True)
    for path in (STAGE / "opt").rglob("*"):  # no group or other write bits
        if not path.is_symlink():
            path.chmod(path.stat().st_mode & ~0o022)
    (STAGE / "usr" / "bin").mkdir(parents=True)
    (STAGE / "usr" / "bin" / "tamra").symlink_to("/opt/tamra/tamra")
    share = STAGE / "usr" / "share"
    icons = share / "icons" / "hicolor"
    _install(LINUX / "tamra.desktop", share / "applications" / "tamra.desktop", 0o644)
    _install(ROOT / "ui" / "src" / "logo.svg", icons / "scalable" / "apps" / "tamra.svg", 0o644)
    _install(ROOT / "packaging" / "tamra.png", icons / "256x256" / "apps" / "tamra.png", 0o644)
    _install(LINUX / "apparmor-tamra", STAGE / "etc" / "apparmor.d" / "tamra", 0o644)
    doc = share / "doc" / "tamra"
    doc.mkdir(parents=True)
    parts = [LINUX / "copyright-header.txt", ROOT / "NOTICE", ROOT / "LICENSE"]
    (doc / "copyright").write_text(
        "\n\n".join(p.read_text(encoding="utf-8").strip() for p in parts) + "\n", encoding="utf-8"
    )
    debian = STAGE / "DEBIAN"
    debian.mkdir()
    for script in ("postinst", "postrm"):
        _install(LINUX / script, debian / script, 0o755)
    (debian / "conffiles").write_text("/etc/apparmor.d/tamra\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in STAGE.rglob("*") if p.is_file() and not p.is_symlink())
    (debian / "control").write_text(
        control(version, depends(BUNDLE), size // 1024 + 1), encoding="utf-8"
    )


def main() -> int:
    if sys.platform != "linux":
        raise SystemExit("build the .deb on Ubuntu")
    if not (BUNDLE / "tamra").is_file():
        raise SystemExit(f"missing {BUNDLE / 'tamra'}; run scripts/build.sh first")
    import tamra

    stage(tamra.__version__)
    out = ROOT / "dist" / f"tamra_{tamra.__version__}_amd64.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(STAGE), str(out)], check=True)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
