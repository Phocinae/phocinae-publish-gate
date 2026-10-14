# -*- coding: utf-8 -*-
"""The command line.

Only `argparse`, and no third-party dependency, matching the family's shape. Every
subcommand the design lists is present; the ones whose measurement backend is not
in this release say so and exit 3 rather than pretending to have checked
something.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

from . import gate
from .bridge import check_kernel_shape, load_kernel
from .errors import PublishGateError
from .version import (
    EXIT_PASS,
    EXIT_USAGE,
    FACTS_SCHEMA,
    KERNEL_DISTRIBUTION,
    KERNEL_SUPPORTED_MAJOR,
    REASON_CODES,
    REPORT_SCHEMA,
    __version__,
)


def _emit(text):
    stream = sys.stdout
    try:
        stream.reconfigure(encoding="utf-8", newline="\n")
    except (AttributeError, ValueError):
        pass
    stream.write(text)
    # Explicit flush. Into a pipe -- which is what CI does, and what `> file`
    # does -- stdout is block-buffered rather than line-buffered, and a report
    # lost at exit reads to the consumer as "not valid JSON: line 1 column 1",
    # which points at the reader instead of at the missing output.
    try:
        stream.flush()
    except (AttributeError, ValueError):
        pass


def _read_json(path, label):
    if not os.path.isfile(path):
        raise PublishGateError(
            "E_INPUT_NOT_FOUND", "%s does not exist: %s" % (label, path), EXIT_USAGE)
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = raw.decode("utf-16")
        elif raw[:3] == b"\xef\xbb\xbf":
            text = raw[3:].decode("utf-8")
        else:
            text = raw.decode("utf-8")
        return json.loads(text.lstrip("\ufeff \t\r\n"))
    except ValueError as exc:
        raise PublishGateError(
            "E_INPUT_NOT_FOUND", "%s is not valid JSON: %s" % (label, exc), EXIT_USAGE)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="phocinae-publish-gate",
        description="Pre-publication checks for media deliverables. Measures facts, "
                    "delegates every verdict to the phocinae-rulepack engine, and "
                    "never writes to the target.",
    )
    parser.add_argument("--version", action="version",
                        version="phocinae-publish-gate %s" % __version__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("gates", help="list the built-in gates, packs and their digests")

    probe = sub.add_parser("probe", help="report what can be measured about a target")
    probe.add_argument("target")
    probe.add_argument("--kind", default="auto")
    probe.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect", help="show a pack's rules and citations")
    inspect.add_argument("target", nargs="?", help="a pack id or path")
    inspect.add_argument("--json", action="store_true")

    check = sub.add_parser("check", help="run the checks and report")
    check.add_argument("target")
    check.add_argument("--kind", default="auto",
                       choices=["auto", "manuscript", "podcast", "music", "declaration"])
    check.add_argument("--pack", action="append", default=None)
    check.add_argument("--packs-dir", default=None)
    check.add_argument("--facts", default=None,
                       help="a phocinae-facts/v1 JSON document to evaluate instead of "
                            "measuring the target")
    check.add_argument("--profile", default=None)
    check.add_argument("--license-allowlist", default=None)
    check.add_argument("--manifest", default=None)
    check.add_argument("--glossary", default=None)
    check.add_argument("--declaration", default=None)
    check.add_argument("--sidecar-dir", default=None)
    check.add_argument("--shownotes", default=None)
    check.add_argument("--ffprobe-cmd", default="ffprobe")
    check.add_argument("--ffmpeg-cmd", default="ffmpeg")
    check.add_argument("--profile-partial", type=float, default=0.0)
    check.add_argument("--timeout-s", type=float, default=20.0)
    check.add_argument("--jobs", type=int, default=1)
    check.add_argument("--content-hash", dest="content_hash", action="store_true", default=False)
    check.add_argument("--no-content-hash", dest="content_hash", action="store_false")
    check.add_argument("--cross-check", dest="cross_check", action="store_true", default=False)
    check.add_argument("--no-cross-check", dest="cross_check", action="store_false")
    check.add_argument("--halfwidth-policy", default="adjacent-cjk",
                       choices=["adjacent-cjk", "strict"])
    check.add_argument("--accept-unverified-basis", action="store_true", default=False)
    check.add_argument("--exit-policy", default="strict", choices=["strict", "content"])
    check.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    check.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    check.add_argument("--tripwire-mode", default="block", choices=["block", "record"])
    check.add_argument("--out", default=None)
    check.add_argument("--scratch-dir", default=None)
    fmt = check.add_mutually_exclusive_group()
    fmt.add_argument("--json", dest="as_json", action="store_true", default=False)
    fmt.add_argument("--text", dest="as_json", action="store_false")
    check.add_argument("--show-pass", action="store_true", default=False)
    check.add_argument("--quiet", action="store_true", default=False)
    check.add_argument("--verbose", action="store_true", default=False)

    explain = sub.add_parser("explain", help="explain one check id")
    explain.add_argument("target")
    explain.add_argument("--check", required=True)
    explain.add_argument("--facts", default=None)

    trip = sub.add_parser("verify-tripwire", help="prove the write counter can fire")
    trip.add_argument("--canary", default=None)

    cap = sub.add_parser("capture-fixtures",
                         help="record real ffprobe/ffmpeg stderr for parser fixtures")
    cap.add_argument("--out", required=True)

    return parser


def _cmd_gates(args):
    kernel = load_kernel()
    lines = [
        "phocinae-publish-gate %s" % __version__,
        "",
        "verdict engine : %s %s (delegated, not reimplemented)"
        % (KERNEL_DISTRIBUTION, kernel.kernel_version),
        "supported major: %s" % ", ".join(str(m) for m in KERNEL_SUPPORTED_MAJOR),
        "facts schema   : %s" % FACTS_SCHEMA,
        "report schema  : %s" % REPORT_SCHEMA,
        "",
        "gates          : q7 manuscript, q8 podcast, q9 music, c6 declaration",
        "",
        "reason codes (%d, closed set):" % len(REASON_CODES),
    ]
    for code in sorted(REASON_CODES):
        lines.append("  %-28s %s" % (code, REASON_CODES[code]))
    _emit("\n".join(lines) + "\n")
    return EXIT_PASS


def _cmd_inspect(args):
    kernel = load_kernel()
    if not args.target:
        raise PublishGateError(
            "E_USAGE", "inspect needs a pack id or path", EXIT_USAGE)
    pack = kernel.load_pack(args.target)
    if args.json:
        _emit(json.dumps({
            "id": pack.id,
            "version": getattr(pack, "version", None),
            "title": getattr(pack, "title", None),
            "rules": [{"id": r.id} for r in pack.rules],
            "citations": [c.id for c in getattr(pack, "citations", [])],
        }, indent=2, sort_keys=True) + "\n")
    else:
        out = ["%s  %s" % (pack.id, getattr(pack, "version", "")),
               "%d rules, %d citations"
               % (len(pack.rules), len(getattr(pack, "citations", [])))]
        for rule in pack.rules:
            out.append("  %s" % rule.id)
        _emit("\n".join(out) + "\n")
    return EXIT_PASS


def _cmd_check(args):
    if args.out and args.dry_run:
        raise PublishGateError(
            "E_OUT_REQUIRES_NO_DRY_RUN",
            "--out was given together with --dry-run. Writing a report is a filesystem "
            "write, and dry-run means no writes. Pass --no-dry-run to write the report, "
            "or drop --out to read it on stdout.",
            EXIT_USAGE,
        )
    facts = _read_json(args.facts, "--facts") if args.facts else None
    pack = args.pack[0] if args.pack else None
    report = gate.run(
        target_path=args.target,
        kind=args.kind,
        facts=facts,
        pack=pack,
        packs_dir=args.packs_dir,
        policy=args.exit_policy,
    )
    if args.as_json:
        _emit(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    else:
        _emit(_render_text(report, show_pass=args.show_pass))
    return report["exit_code"]


def _render_text(report, show_pass=False):
    lines = []
    target = report["target"]
    lines.append("target : %s (%s)" % (target["path"], target["kind"]))
    lines.append("engine : %s %s" % (report["kernel"]["distribution"],
                                    report["kernel"]["version"]))
    counts = report["counts"]
    lines.append("counts : pass=%d fail=%d review=%d skip=%d"
                 % (counts["pass"], counts["fail"], counts["review"], counts["skip"]))
    lines.append("")
    for check in report["checks"]:
        if check["status"] == "pass" and not show_pass:
            continue
        lines.append("  %-10s %-34s %s"
                     % (check["status"].upper(), check["check_id"],
                        (check["detail"] or "")[:90]))
    if report["unavailable_facts"]:
        lines.append("")
        lines.append("unavailable facts (recorded as unavailable, not as zero):")
        for name in report["unavailable_facts"]:
            lines.append("  %s" % name)
    lines.append("")
    lines.append("status : %s   exit %d (%s)"
                 % (report["status"].upper(), report["exit_code"], report["exit_basis"]))
    if counts["skip"]:
        lines.append("SKIP is not PASS: %d check(s) did not run." % counts["skip"])
    return "\n".join(lines) + "\n"


def _cmd_explain(args):
    kernel = load_kernel()
    facts = _read_json(args.facts, "--facts") if args.facts else None
    report = gate.run(target_path=args.target, facts=facts, pack=None)
    for check in report["checks"]:
        if check["check_id"] == args.check:
            _emit(json.dumps(check, indent=2, sort_keys=True) + "\n")
            return EXIT_PASS
    raise PublishGateError(
        "E_INPUT_NOT_FOUND",
        "no check with id %r in this run; ids present: %s"
        % (args.check, ", ".join(c["check_id"] for c in report["checks"]) or "(none)"),
        EXIT_USAGE,
    )


def _cmd_not_implemented(args, what):
    raise PublishGateError(
        "E_USAGE",
        "%s is not implemented in this release (%s). It is listed in the command "
        "surface on purpose: a subcommand that silently does nothing is worse than "
        "one that says it is missing." % (args.command, what),
        EXIT_USAGE,
    )


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_PASS
    try:
        if args.command == "gates":
            return _cmd_gates(args)
        if args.command == "inspect":
            return _cmd_inspect(args)
        if args.command == "check":
            return _cmd_check(args)
        if args.command == "explain":
            return _cmd_explain(args)
        if args.command == "probe":
            return _cmd_not_implemented(args, "no measurement backend yet")
        if args.command == "verify-tripwire":
            return _cmd_not_implemented(args, "no write tripwire yet")
        if args.command == "capture-fixtures":
            return _cmd_not_implemented(args, "no capture backend yet")
        raise PublishGateError("E_USAGE", "unknown command %r" % args.command, EXIT_USAGE)
    except PublishGateError as exc:
        sys.stderr.write("ERROR %s\n" % exc)
        if exc.detail:
            for item in exc.detail:
                sys.stderr.write("      %s\n" % item)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
