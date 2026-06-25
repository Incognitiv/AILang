from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AILANG = REPO_ROOT / "ailang.py"


def _available_c_compiler() -> str | None:
    return shutil.which("gcc") or shutil.which("clang")


def test_cabi_header_block_emits_bridge_header(tmp_path: Path) -> None:
    src = tmp_path / "bridge_headers.ail"
    out = tmp_path / "include"
    src.write_text(
        '''\
abi header "sys/event.h":
    guard AILANG_BRIDGE_SYS_EVENT_H
    include <time.h>
    define EVFILT_READ = -1
    define EV_ADD = 0x0001
    define EV_CLEAR = 0x0020

    struct kevent:
        ident: uintptr_t
        filter: short
        flags: ushort
        fflags: uint
        data: intptr_t
        udata: pointer
    end

    macro EV_SET(kevp, a, b, c, d, e, f):
        c_emit """
        do {
            (kevp)->ident = (a);
            (kevp)->filter = (b);
            (kevp)->flags = (c);
            (kevp)->fflags = (d);
            (kevp)->data = (e);
            (kevp)->udata = (f);
        } while (0)
        """
    end

    prototype int kqueue()
    prototype int kevent(
        kq: int,
        changelist: const_pointer,
        nchanges: int,
        eventlist: pointer,
        nevents: int,
        timeout: const_pointer
    )
end
''',
        encoding="utf-8",
    )

    check_proc = subprocess.run(
        [sys.executable, str(AILANG), str(src), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert check_proc.returncode == 0, check_proc.stderr or check_proc.stdout

    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            "--emit-abi-headers",
            "-o",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    header = out / "sys" / "event.h"
    text = header.read_text(encoding="utf-8")
    assert "#ifndef AILANG_BRIDGE_SYS_EVENT_H" in text
    assert "#include <time.h>" in text
    assert "#define EVFILT_READ -1" in text
    assert "#define EV_ADD 0x0001" in text
    assert "struct kevent {" in text
    assert "uintptr_t ident;" in text
    assert "unsigned short flags;" in text
    assert "void * udata;" in text
    assert "#define EV_SET(kevp, a, b, c, d, e, f)" in text
    assert "int kqueue(void);" in text
    assert "int kevent(" in text
    assert "const void * changelist" in text


def test_cabi_header_output_compiles_as_c(tmp_path: Path) -> None:
    cc = _available_c_compiler()
    if cc is None:
        pytest.skip("no C compiler available")

    src = tmp_path / "bridge_headers.ail"
    out = tmp_path / "include"
    consumer = tmp_path / "consumer.c"
    obj = tmp_path / "consumer.o"
    src.write_text(
        '''\
abi header "sys/event.h":
    include <time.h>
    define EVFILT_READ = -1
    define EV_ADD = 0x0001

    struct kevent:
        ident: uintptr_t
        filter: short
        flags: ushort
        fflags: uint
        data: intptr_t
        udata: pointer
    end

    macro EV_SET(kevp, a, b, c, d, e, f):
        c_emit """
        do {
            (kevp)->ident = (a);
            (kevp)->filter = (b);
            (kevp)->flags = (c);
            (kevp)->fflags = (d);
            (kevp)->data = (e);
            (kevp)->udata = (f);
        } while (0)
        """
    end
end

abi header "libcasper.h":
    typedef cap_channel_t = "struct ailang_cap_channel"
    struct ailang_cap_channel:
        unused: int
    end
    prototype "cap_channel_t *" cap_init()
    prototype int cap_close(channel: "cap_channel_t *")
end
''',
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(AILANG), str(src), "--emit-abi-headers", "-o", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    consumer.write_text(
        """\
#include "sys/event.h"

int main(void) {
    struct kevent ev;
    EV_SET(&ev, 1, EVFILT_READ, EV_ADD, 0, 0, 0);
    return ev.filter == EVFILT_READ ? 0 : 1;
}
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [cc, "-std=gnu23", "-I", str(out), "-c", str(consumer), "-o", str(obj)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_legacy_cabi_header_spelling_still_emits(tmp_path: Path) -> None:
    src = tmp_path / "legacy_bridge_headers.ail"
    out = tmp_path / "include"
    src.write_text(
        '''\
cabi header "legacy.h":
    define LEGACY_ABI_HEADER = 1
end
''',
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            "--emit-cabi-headers",
            "-o",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = (out / "legacy.h").read_text(encoding="utf-8")
    assert "#define LEGACY_ABI_HEADER 1" in text


def test_abi_header_advanced_c_surface(tmp_path: Path) -> None:
    src = tmp_path / "advanced_bridge_headers.ail"
    out = tmp_path / "include"
    src.write_text(
        '''\
abi header "advanced.h":
    include_next <sys/types.h>

    ifndef ADVANCED_LIMIT:
        define ADVANCED_LIMIT = 64
    end

    if "defined(__linux__) && !defined(ADVANCED_PLATFORM)":
        define ADVANCED_PLATFORM = 1
    else:
        define ADVANCED_PLATFORM = 0
    end

    prototype int advanced_open(path: cstring, flags: int, ...)

    macro ADVANCED_CALL(...):
        c_emit """
        advanced_open(__VA_ARGS__)
        """
    end

    static inline int advanced_add_one(value: int):
        c_emit """
        return value + 1;
        """
    end
end
''',
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(AILANG),
            str(src),
            "--emit-abi-headers",
            "-o",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    text = (out / "advanced.h").read_text(encoding="utf-8")
    assert "#include_next <sys/types.h>" in text
    assert "#ifndef ADVANCED_LIMIT" in text
    assert "#if defined(__linux__) && !defined(ADVANCED_PLATFORM)" in text
    assert "#else" in text
    assert "int advanced_open(const char * path, int flags, ...);" in text
    assert "#define ADVANCED_CALL(...) advanced_open(__VA_ARGS__)" in text
    assert "static inline int\nadvanced_add_one(int value)\n{" in text
    assert "return value + 1;" in text
