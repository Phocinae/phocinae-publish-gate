# -*- coding: utf-8 -*-
"""Assert the message a user sees when the kernel is missing, and what it omits.

This exists because the exit code alone is not the contract. A tool that exits 3
with the message "No module named 'phocinae_rulepack'" has the right exit code and
still leaves the user to work out what to install, which package provides it, and
why the tool needs it at all.

The check is deliberately specific about the two things a user acts on -- the
package name and the install command -- and about one thing they must *not* see:
a bare Python traceback, which would mean the failure escaped this project's error
handling and is being reported by the interpreter rather than by the tool.

Usage:
    python tools/check_kernel_absent.py err.txt
"""

from __future__ import annotations

import io
import os
import sys


def _read_text(path):
    """Read captured stderr however the shell happened to write it.

    Read as bytes and decode, rather than opening in text mode, because the
    producer is a *shell redirection* and shells disagree: bash writes UTF-8 with
    no BOM, PowerShell's `2>` writes UTF-16LE with a BOM.

    This exact mistake has now cost a debugging cycle twice in this project's
    family, and it fails in the worst possible way for a *checker*: the file
    contains the expected text, the checker reports that it does not, and the
    person reading the failure goes looking for a bug in the program under test.
    A checker must not be the thing that is wrong.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-16", errors="replace")


def main(argv):
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    if len(argv) != 2:
        stream.write("usage: check_kernel_absent.py <stderr-file>\n")
        return 2
    path = argv[1]
    if not os.path.isfile(path):
        stream.write("FAIL no stderr was captured at %s\n" % path)
        return 1
    text = _read_text(path)

    failures = []
    if "E_RULEPACK_UNAVAILABLE" not in text:
        failures.append("the registered reason code E_RULEPACK_UNAVAILABLE is absent, so "
                        "the failure cannot be looked up")
    if "pip install phocinae-rulepack" not in text:
        failures.append("the message does not name the install command, so the user has "
                        "to work out what to install")
    if "verdict" not in text:
        failures.append("the message does not say why the dependency exists, which is the "
                        "difference between an instruction and an explanation")
    if "Traceback (most recent call last)" in text:
        failures.append("a Python traceback was printed: the failure escaped this project's "
                        "error handling and is being reported by the interpreter")
    if "No module named" in text and "pip install" not in text:
        failures.append("the raw ImportError text reached the user without an instruction")

    if failures:
        for failure in failures:
            stream.write("FAIL %s\n" % failure)
        stream.write("\n--- captured stderr ---\n%s\n" % text[:1500])
        return 1

    stream.write("OK the kernel-absent message names the reason code, the install "
                 "command and the reason, and is not a traceback\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
