#!/usr/bin/env python3
"""Inventory executable tools visible in a WSL PATH.

The original one-liner is good for a quick tree, but it counts every PATH
entry as equal. This script separates unique command names, duplicate names,
symlinks, and broad categories so the result is usable for Mixtar userland
planning.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DEFAULT_DISTRO = "Debian"
DEFAULT_EXCLUDE_SUBSTRINGS = ("/mnt/c", "/mnt/z", ".cargo", "venv")
TREE_OUT = Path("wsl_tools_tree_counted.txt")
REPORT_OUT = Path("wsl_tools_inventory.md")
JSON_OUT = Path("wsl_tools_inventory.json")


@dataclass(frozen=True)
class Entry:
    directory: str
    real_directory: str
    name: str
    path: str
    kind: str
    target: str


def run_wsl(distro: str, script: str) -> str:
    proc = subprocess.run(
        ["wsl.exe", "-d", distro, "--", "bash", "-lc", script],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"wsl-tools-inventory: WSL command failed:\n{proc.stderr}")
    return proc.stdout


def shell_literal(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def collect_entries(distro: str, excludes: tuple[str, ...]) -> tuple[list[str], dict[str, str], list[Entry]]:
    exclude_expr = " ".join(shell_literal(item) for item in excludes)
    script = rf'''
set -e
seen_dirs=\$(mktemp)
trap 'rm -f "\$seen_dirs"' EXIT
printf '%s\n' "\$PATH" | tr ':' '\n' | while IFS= read -r d; do
  [ -n "\$d" ] || continue
  printf 'PATH\t%s\n' "\$d"
done
printf '%s\n' "\$PATH" | tr ':' '\n' | while IFS= read -r d; do
  [ -n "\$d" ] || continue
  skip=0
  for needle in {exclude_expr}; do
    case "\$d" in
      *"\$needle"*) skip=1 ;;
    esac
  done
  [ "\$skip" -eq 0 ] || continue
  [ -d "\$d" ] || continue
  real=\$(readlink -f "\$d" 2>/dev/null || printf '%s' "\$d")
  if grep -Fxq "\$real" "\$seen_dirs"; then
    printf 'SKIP_DUP_DIR\t%s\t%s\n' "\$d" "\$real"
    continue
  fi
  printf '%s\n' "\$real" >> "\$seen_dirs"
  printf 'DIR\t%s\t%s\n' "\$d" "\$real"
  find -L "\$d" -maxdepth 1 \( -type f -o -type l \) -perm /111 -print 2>/dev/null |
    while IFS= read -r p; do
      [ -n "\$p" ] || continue
      base=\$(basename "\$p")
      kind=file
      target=
      if [ -L "\$p" ]; then
        kind=symlink
        target=\$(readlink "\$p" 2>/dev/null || true)
      fi
      printf 'ENTRY\t%s\t%s\t%s\t%s\t%s\t%s\n' "\$d" "\$real" "\$base" "\$p" "\$kind" "\$target"
    done
done
'''
    raw_paths: list[str] = []
    kept_dirs: dict[str, str] = {}
    entries: list[Entry] = []
    for line in run_wsl(distro, script).splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        tag = parts[0]
        if tag == "PATH" and len(parts) >= 2:
            raw_paths.append(parts[1])
        elif tag == "DIR" and len(parts) >= 3:
            kept_dirs[parts[1]] = parts[2]
        elif tag == "ENTRY" and len(parts) >= 7:
            entries.append(Entry(parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]))
    return raw_paths, kept_dirs, entries


def starts_any(prefixes: tuple[str, ...]) -> Callable[[str], bool]:
    return lambda name: name.startswith(prefixes)


def exact_or_prefix(exact: set[str], prefixes: tuple[str, ...]) -> Callable[[str], bool]:
    return lambda name: name in exact or name.startswith(prefixes)


CORE_POSIX = {
    "[",
    "arch",
    "basename",
    "cat",
    "chgrp",
    "chmod",
    "chown",
    "cksum",
    "cmp",
    "comm",
    "cp",
    "csplit",
    "cut",
    "date",
    "dd",
    "df",
    "dirname",
    "du",
    "echo",
    "env",
    "expand",
    "expr",
    "false",
    "find",
    "fmt",
    "fold",
    "grep",
    "head",
    "hostname",
    "id",
    "join",
    "kill",
    "ln",
    "logname",
    "ls",
    "mkdir",
    "mkfifo",
    "mknod",
    "mktemp",
    "mv",
    "nice",
    "nl",
    "nohup",
    "paste",
    "printf",
    "pwd",
    "readlink",
    "realpath",
    "rm",
    "rmdir",
    "seq",
    "sleep",
    "sort",
    "split",
    "stat",
    "stty",
    "sync",
    "tail",
    "tee",
    "test",
    "touch",
    "tr",
    "true",
    "tty",
    "uname",
    "unexpand",
    "uniq",
    "wc",
    "who",
    "whoami",
    "xargs",
    "yes",
}


CATEGORIES: list[tuple[str, Callable[[str], bool]]] = [
    ("core-posix-like", lambda name: name in CORE_POSIX),
    (
        "debian-packaging",
        exact_or_prefix(
            {"debuild", "debchange", "dch"},
            ("apt", "dpkg", "debconf", "deb-systemd", "dh_", "dh-", "dselect"),
        ),
    ),
    (
        "toolchain-build-debug",
        exact_or_prefix(
            {
                "ar",
                "as",
                "autoconf",
                "autoheader",
                "automake",
                "autoreconf",
                "bison",
                "cc",
                "cmake",
                "cpack",
                "ctest",
                "flex",
                "g++",
                "gcc",
                "ld",
                "m4",
                "make",
                "nm",
                "objcopy",
                "objdump",
                "pkg-config",
                "pkgconf",
                "ranlib",
                "strip",
            },
            (
                "aclocal",
                "addr2line",
                "asan_",
                "automake",
                "c89-",
                "c99-",
                "clang",
                "g++",
                "gcc",
                "gcov",
                "gprof",
                "ld.",
                "llvm",
                "x86_64-linux-gnu",
            ),
        ),
    ),
    (
        "language-runtime",
        exact_or_prefix(
            {"node", "perl", "python3", "ruby"},
            ("cargo", "go", "npm", "npx", "perl", "pip", "python", "ruby", "rust"),
        ),
    ),
    (
        "desktop-x-gtk-dbus-font",
        exact_or_prefix(
            {"alacritty", "Xephyr"},
            ("appres", "atobm", "bitmap", "dbus", "fc-", "font", "gapplication", "gdbus", "gdk", "gio", "gsettings", "gtk", "pango", "x", "X"),
        ),
    ),
    (
        "systemd-service-management",
        exact_or_prefix(
            {"busctl", "journalctl", "loginctl", "machinectl", "networkctl", "resolvectl", "timedatectl"},
            ("systemd",),
        ),
    ),
    (
        "compression-archive",
        exact_or_prefix(
            {"cpio", "gzip", "tar", "unzip", "zip"},
            ("7z", "bz", "gz", "unzip", "xz", "zcat", "zcmp", "zdiff", "zegrep", "zfgrep", "zgrep", "zip"),
        ),
    ),
    (
        "network-remote",
        exact_or_prefix(
            {"curl", "scp", "sftp", "ssh", "wget"},
            ("ssh", "ssl", "curl", "wget", "scp", "sftp"),
        ),
    ),
]


def category_for(name: str) -> str:
    for category, predicate in CATEGORIES:
        if predicate(name):
            return category
    return "other"


def md_list(values: list[str], limit: int = 120) -> str:
    if not values:
        return "_none_"
    visible = values[:limit]
    suffix = "" if len(values) <= limit else f"\n\n...and {len(values) - limit} more."
    return "`" + "`, `".join(visible) + "`" + suffix


def write_tree(path: Path, kept_dirs: dict[str, str], by_dir: dict[str, list[Entry]]) -> None:
    lines: list[str] = []
    total = sum(len(values) for values in by_dir.values())
    lines.append(f"# WSL PATH executable tree, counted")
    lines.append("")
    lines.append(f"Total executable path entries: {total}")
    lines.append(f"Directories scanned: {len(kept_dirs)}")
    for directory, real_directory in kept_dirs.items():
        values = sorted(by_dir.get(directory, []), key=lambda e: e.name)
        lines.append("")
        if real_directory == directory:
            lines.append(f"DIR {directory} ({len(values)} entries)")
        else:
            lines.append(f"DIR {directory} -> {real_directory} ({len(values)} entries)")
        for entry in values:
            marker = " -> " + entry.target if entry.kind == "symlink" and entry.target else ""
            lines.append(f"  - {entry.name} [{entry.kind}]{marker}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(path: Path, raw_paths: list[str], kept_dirs: dict[str, str], entries: list[Entry]) -> None:
    unique_names = sorted({entry.name for entry in entries})
    by_name: dict[str, list[Entry]] = defaultdict(list)
    by_dir: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        by_name[entry.name].append(entry)
        by_dir[entry.directory].append(entry)

    duplicate_names = sorted(name for name, values in by_name.items() if len(values) > 1)
    symlinks = [entry for entry in entries if entry.kind == "symlink"]
    category_names: dict[str, list[str]] = defaultdict(list)
    for name in unique_names:
        category_names[category_for(name)].append(name)

    lines: list[str] = []
    lines.append("# WSL Tools Inventory")
    lines.append("")
    lines.append("This report counts executable commands visible through the filtered WSL PATH.")
    lines.append("It is not a Linux-kernel tool list and it is not a minimal MixtarRVS base list.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Raw PATH directories: `{len(raw_paths)}`")
    lines.append(f"- Scanned unique physical PATH directories after filters: `{len(kept_dirs)}`")
    lines.append(f"- Executable path entries: `{len(entries)}`")
    lines.append(f"- Unique command names: `{len(unique_names)}`")
    lines.append(f"- Duplicate command names across PATH: `{len(duplicate_names)}`")
    lines.append(f"- Symlink executable entries: `{len(symlinks)}`")
    lines.append("")
    lines.append("## Scanned Directories")
    lines.append("")
    for directory, real_directory in kept_dirs.items():
        suffix = "" if real_directory == directory else f" -> `{real_directory}`"
        lines.append(f"- `{directory}`{suffix}: `{len(by_dir[directory])}` entries")
    lines.append("")
    lines.append("## Category Counts")
    lines.append("")
    for category in sorted(category_names):
        lines.append(f"- `{category}`: `{len(category_names[category])}` unique commands")
    lines.append("")
    lines.append("## Core/POSIX-Like Commands Found")
    lines.append("")
    lines.append(md_list(sorted(category_names.get("core-posix-like", []))))
    lines.append("")
    lines.append("## Debian Packaging Commands")
    lines.append("")
    lines.append(md_list(sorted(category_names.get("debian-packaging", []))))
    lines.append("")
    lines.append("## Toolchain/Build/Debug Commands")
    lines.append("")
    lines.append(md_list(sorted(category_names.get("toolchain-build-debug", []))))
    lines.append("")
    lines.append("## Desktop/X/GTK/DBus/Font Commands")
    lines.append("")
    lines.append(md_list(sorted(category_names.get("desktop-x-gtk-dbus-font", []))))
    lines.append("")
    lines.append("## Duplicate Command Names")
    lines.append("")
    if duplicate_names:
        for name in duplicate_names:
            paths = ", ".join(f"`{entry.path}`" for entry in by_name[name])
            lines.append(f"- `{name}`: {paths}")
    else:
        lines.append("_none_")
    lines.append("")
    lines.append("## MixtarRVS Interpretation")
    lines.append("")
    lines.append("- Treat `core-posix-like` as the only set even close to a base-userland candidate.")
    lines.append("- Treat Debian packaging, systemd, desktop, language runtimes, and toolchains as optional tiers.")
    lines.append("- Do not use this file as a target list for MixtarRVS. It measures one installed Debian WSL environment.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(path: Path, raw_paths: list[str], kept_dirs: dict[str, str], entries: list[Entry]) -> None:
    unique_names = sorted({entry.name for entry in entries})
    category_unique_counts = Counter(category_for(name) for name in unique_names)
    category_entry_counts = Counter(category_for(entry.name) for entry in entries)
    payload = {
        "raw_path_directories": raw_paths,
        "scanned_directories": kept_dirs,
        "entries": [entry.__dict__ for entry in entries],
        "unique_commands": unique_names,
        "category_unique_counts": dict(category_unique_counts),
        "category_entry_counts": dict(category_entry_counts),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--distro", default=DEFAULT_DISTRO)
    parser.add_argument("--tree-out", default=str(TREE_OUT))
    parser.add_argument("--report-out", default=str(REPORT_OUT))
    parser.add_argument("--json-out", default=str(JSON_OUT))
    parser.add_argument("--exclude", action="append", default=list(DEFAULT_EXCLUDE_SUBSTRINGS))
    args = parser.parse_args()

    raw_paths, kept_dirs, entries = collect_entries(args.distro, tuple(args.exclude))
    by_dir: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        by_dir[entry.directory].append(entry)

    write_tree(Path(args.tree_out), kept_dirs, by_dir)
    write_report(Path(args.report_out), raw_paths, kept_dirs, entries)
    write_json(Path(args.json_out), raw_paths, kept_dirs, entries)

    unique = {entry.name for entry in entries}
    print(f"wsl-tools-inventory: executable entries={len(entries)} unique_commands={len(unique)}")
    print(f"wsl-tools-inventory: wrote {args.tree_out}, {args.report_out}, {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
