"""scripts/make_deb.py: the control file and the package layout (Ubuntu support spec §4.3)."""

from make_deb import LINUX, WEBENGINE_PROCESS, _owner, control


def test_the_owner_of_a_library_is_read_past_diversions():
    out = (
        "diversion by libc6 from: /lib64/ld-linux-x86-64.so.2\n"
        "diversion by libc6 to: /lib64/ld-linux-x86-64.so.2.usr-is-merged\n"
        "libc6:amd64: /lib64/ld-linux-x86-64.so.2\n"
    )
    assert _owner(out) == "libc6"
    assert _owner("libnss3:amd64: /usr/lib/x86_64-linux-gnu/libnss3.so\n") == "libnss3"
    assert _owner("libgl1:amd64, libgl1:i386: /usr/lib/x.so\n") == "libgl1"
    assert _owner("") is None


def test_the_control_file_names_the_package_and_its_dependencies():
    text = control("0.2.0", ["libc6", "libnss3"], 123456)
    fields = dict(
        line.split(": ", 1) for line in text.splitlines() if ": " in line and line[0] != " "
    )
    assert fields["Package"] == "tamra"
    assert fields["Version"] == "0.2.0"
    assert fields["Architecture"] == "amd64"
    assert fields["Depends"] == "libc6, libnss3"
    assert fields["Installed-Size"] == "123456"
    assert "gnome-keyring" in fields["Recommends"]
    assert text.endswith("\n")
    # Description continuation lines start with a space, as dpkg requires.
    assert all(line.startswith(" ") for line in text.split("Description: ", 1)[1].splitlines()[1:])


def test_the_apparmor_profile_names_the_web_engine_process():
    profile = (LINUX / "apparmor-tamra").read_text(encoding="utf-8")
    assert "userns," in profile
    assert WEBENGINE_PROCESS.removeprefix("_internal/") in profile


def test_the_maintainer_scripts_are_posix_sh_with_unix_line_ends():
    for name in ("postinst", "postrm"):
        data = (LINUX / name).read_bytes()
        assert data.startswith(b"#!/bin/sh\n") and b"\r" not in data
