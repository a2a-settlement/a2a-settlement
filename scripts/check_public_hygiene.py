#!/usr/bin/env python3
"""Fail when a tracked file carries material that belongs in a private repository.

This repository is public. Notes about one particular production host (its
address, its service user, its numbered deploy plans) are kept elsewhere. The
check reads every tracked text file and reports:

* a path that looks like a deploy plan or host runbook;
* a host-specific word from ``WORDS``;
* a public IPv4 address that is not on the allowlist.

Run from the repository root: ``python scripts/check_public_hygiene.py``.
To accept a finding that is intentional, add the address to ``ALLOWED_ADDRESSES``
or the path to ``ALLOWED_PATHS`` in this file, in the same change, with a reason.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
from pathlib import Path

# Deploy plans and host runbooks, wherever they sit in the tree.
PLAN_PATHS = re.compile(r"(^|/)[A-Z0-9_]+_PLAN\.md$|(^|/)deploy-plans/")

# Words that only make sense for one particular host. Matched case-insensitively
# as whole words. Each is assembled from parts so this file does not match itself.
WORDS = [
    "claw" + "dbot",
    "do-" + "droplet",
    "drop" + "let plan",
]
WORD_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(map(re.escape, WORDS)) + r")(?![A-Za-z0-9])", re.I)

IPV4_RE = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w]|\.\d)")

# Public addresses that are meant to be here. Give a reason for each.
ALLOWED_ADDRESSES: dict[str, str] = {
    "8.8.8.8": "public DNS resolver used as an example",
    "8.8.4.4": "public DNS resolver used as an example",
    "1.1.1.1": "public DNS resolver used as an example",
    "1.0.0.1": "public DNS resolver used as an example",
    "1.2.3.4": "placeholder address in tests and examples",
}

# Paths that are not scanned for addresses or words (lock files and the like).
ALLOWED_PATHS = re.compile(
    r"(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|uv\.lock|poetry\.lock)$"
    r"|\.(png|jpg|jpeg|gif|ico|svg|woff2?|ttf|pdf|map|min\.js)$"
    r"|(^|/)(dist|dist-[^/]+|build|node_modules)/"
)

# A version string such as ``Chrome/120.0.0.0`` or ``v1.2.3.4`` is not an address.
VERSION_PREFIX = re.compile(r"(?:[vV]|/|version[ =:\"']+|==|>=|<=|~=|\^)$")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    return [name for name in out.decode("utf-8", "replace").split("\0") if name]


def public_address(match: re.Match[str], line: str) -> str | None:
    text = match.group(0)
    if any(len(part) > 1 and part.startswith("0") for part in match.groups()):
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if not address.is_global:
        return None
    if text in ALLOWED_ADDRESSES:
        return None
    if VERSION_PREFIX.search(line[: match.start()]):
        return None
    return text


def main() -> int:
    findings: list[str] = []
    for name in tracked_files():
        if PLAN_PATHS.search(name):
            findings.append(f"{name}: looks like a deploy plan or host runbook")
        if ALLOWED_PATHS.search(name):
            continue
        path = Path(name)
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            word = WORD_RE.search(line)
            if word:
                findings.append(f"{name}:{number}: host-specific word {word.group(1)!r}")
            for match in IPV4_RE.finditer(line):
                address = public_address(match, line)
                if address:
                    findings.append(f"{name}:{number}: public address {address}")
    if findings:
        print("Material that belongs in a private repository:\n")
        for finding in findings:
            print("  " + finding)
        print(f"\n{len(findings)} finding(s). See the docstring of {Path(__file__).name}.")
        return 1
    print("public hygiene check: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
