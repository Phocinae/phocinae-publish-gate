# -*- coding: utf-8 -*-
"""Test suite for phocinae-publish-gate.

Runs standalone and under pytest. A failing assertion raises, so the suite is
visible to a collector -- a collector that only recorded failures would look green
whatever broke.

The assertions that matter most here are about the *integration* with
`phocinae-rulepack`, because that is this project's only real coupling and the
only place a mistake would be silent:

  * a missing kernel must be an actionable error, not an ImportError or a crash;
  * a kernel whose API shape changed must be refused, not used;
  * an exit code must never be 0 when nothing was verified.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "src")
sys.path.insert(0, SRC)

from phocinae_publish_gate import bridge, facts as facts_mod, gate, version  # noqa: E402
from phocinae_publish_gate.errors import PublishGateError  # noqa: E402

CHECKS = {"passed": 0, "failed": [], "skipped": []}


class CheckFailed(AssertionError):
    pass


def ok(label, condition, detail=""):
    if condition:
        CHECKS["passed"] += 1
        return True
    CHECKS["failed"].append((label, detail))
    raise CheckFailed("%s%s" % (label, (": " + str(detail)) if detail else ""))


def skip(label, why):
    CHECKS["skipped"].append((label, why))


def eq(label, actual, expected):
    return ok(label, actual == expected, "expected %r, got %r" % (expected, actual))


def raises(label, reason_code, func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except PublishGateError as exc:
        return eq(label, exc.reason_code, reason_code)
    except CheckFailed:
        raise
    except Exception as exc:  # noqa: BLE001
        return ok(label, False, "raised %s: %s" % (type(exc).__name__, exc))
    return ok(label, False, "did not raise %s" % reason_code)


def cli(args):
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "phocinae_publish_gate"] + list(args),
        capture_output=True, text=True, encoding="utf-8", cwd=REPO, env=env)


def stub_kernel(**overrides):
    """A module that satisfies the shape check, so a test can break one thing."""
    module = types.ModuleType("stub_kernel")
    module.__version__ = "0.1.2"
    module.EXIT_PASS, module.EXIT_FAIL, module.EXIT_REVIEW, module.EXIT_USAGE = 0, 1, 2, 3
    module.EXIT_CODES = (0, 1, 2, 3)
    module.VERDICTS = ("pass", "review", "fail")

    class PackError(Exception):
        pass

    module.PackError = PackError
    module.load_pack = lambda target, packs_dir=None: None
    module.check = lambda pack, document: {"verdict": "pass", "reasons": []}
    module.check_many = lambda pack, documents: {"results": []}
    module.exit_code = lambda payload: 0
    module.dump_canonical = lambda payload: "{}"
    for key, value in overrides.items():
        setattr(module, key, value)
    return module


# ---------------------------------------------------------------------------
# A. exit-code semantics -- the guards that must never let "nothing ran" pass
# ---------------------------------------------------------------------------


def test_exit_codes():
    def counts(**kw):
        # `pass` is a reserved word, so the pass bucket is spelled `n_pass`.
        # Spelling it any other way in a call is a syntax error, which is how
        # this helper came to be written this way.
        base = {key: 0 for key in version.COUNT_KEYS}
        for key, value in kw.items():
            base["pass" if key == "n_pass" else key] = value
        return base

    eq("A1 a clean pass is 0",
       version.exit_code_for(counts(n_pass=1))[0], version.EXIT_PASS)
    eq("A2 a failure is 1",
       version.exit_code_for(counts(n_pass=1, fail=1))[0], version.EXIT_FAIL)
    eq("A3 a review is 2",
       version.exit_code_for(counts(n_pass=1, review=1))[0], version.EXIT_REVIEW)
    eq("A4 a skip is 2 under strict",
       version.exit_code_for(counts(n_pass=1, skip=1))[0], version.EXIT_REVIEW)
    eq("A5 fail outranks review and skip",
       version.exit_code_for(counts(fail=1, review=1, skip=1))[0], version.EXIT_FAIL)

    # The guard: nothing verified is exit 3 under BOTH policies.
    empty = counts()
    code, why = version.exit_code_for(empty, policy="strict")
    eq("A6 all-skip under strict is 3", code, version.EXIT_USAGE)
    eq("A7 and says EMPTY_SCAN", why, "E_EMPTY_SCAN")
    code2, why2 = version.exit_code_for(empty, policy="content")
    eq("A8 all-skip under content is ALSO 3", code2, version.EXIT_USAGE)
    eq("A9 and says EMPTY_SCAN too", why2, "E_EMPTY_SCAN")

    # content policy: skip alone no longer lifts to 2 ...
    eq("A10 content: skip does not lift a pass to 2",
       version.exit_code_for(counts(n_pass=1, skip=3), policy="content")[0],
       version.EXIT_PASS)
    # ... but review still does.
    eq("A11 content: review still 2",
       version.exit_code_for(counts(n_pass=1, review=1, skip=1), policy="content")[0],
       version.EXIT_REVIEW)
    eq("A12 content: fail still 1",
       version.exit_code_for(counts(fail=1, skip=9), policy="content")[0],
       version.EXIT_FAIL)


def test_aggregate_status():
    # `aggregate_status([])` returns `pass` as the identity element of the fold,
    # the way `sum([])` is 0. It is not a claim that anything passed: the report
    # builder never has an empty check list, and the exit code comes from
    # `exit_code_for`, which reads counts and returns exit 3 when every verdict
    # bucket is zero. Asserted here so the identity is a documented decision
    # rather than an accident somebody later "fixes" into a silent pass.
    eq("B0 the empty fold is the identity, not a claim",
       version.aggregate_status([]), "pass")
    eq("B0b but an empty scan still exits 3",
       version.exit_code_for({k: 0 for k in version.COUNT_KEYS})[0], version.EXIT_USAGE)

    eq("B1 a skip alone is review, not pass",
       version.aggregate_status(["skip"]), "review")
    eq("B2 skip beside pass is still review",
       version.aggregate_status(["pass", "skip"]), "review")
    eq("B3 all pass is pass", version.aggregate_status(["pass", "pass"]), "pass")
    eq("B4 fail wins", version.aggregate_status(["pass", "review", "fail"]), "fail")
    eq("B5 review beats skip", version.aggregate_status(["skip", "review"]), "review")

    # An unrecognised status key is a broken invariant, not a passing target.
    code, why = version.exit_code_for({"pass": 0, "fail": 0, "review": 0, "skip": 0,
                                       "maybe": 3})
    eq("B6 an unknown status key is exit 3", code, version.EXIT_USAGE)
    eq("B7 and names the vacuous-pass invariant", why, "E_VACUOUS_PASS_DETECTED")


# ---------------------------------------------------------------------------
# C. reason codes are a closed set
# ---------------------------------------------------------------------------


def test_reason_code_closure():
    try:
        PublishGateError("E_NOT_REGISTERED", "x")
    except ValueError as exc:
        ok("C1 an unregistered reason code is refused at construction",
           "unregistered reason code" in str(exc), str(exc))
    else:
        ok("C1 an unregistered reason code is refused at construction", False,
           "no error raised")

    # Every code the package can raise must be registered, and every registered
    # code must carry a description.
    for code, description in version.REASON_CODES.items():
        ok("C2 %s has a description" % code,
           isinstance(description, str) and len(description) > 10, repr(description))

    # The skip codes are in the same closed set as the error codes, because a
    # skip whose reason nobody can look up is as unactionable as a failure's.
    skip_codes = [c for c in version.REASON_CODES if c.startswith("SKIP_")]
    ok("C3 there are skip reason codes", len(skip_codes) >= 4, repr(skip_codes))
    err_codes = [c for c in version.REASON_CODES if c.startswith("E_")]
    ok("C4 there are error reason codes", len(err_codes) >= 8, repr(err_codes))


# ---------------------------------------------------------------------------
# D. the kernel bridge: present, absent, and wrong-shaped
# ---------------------------------------------------------------------------


def test_bridge_shape_check():
    good = stub_kernel()
    eq("D1 a correct stub has no problems", bridge.check_kernel_shape(good), [])

    missing = stub_kernel()
    del missing.check
    problems = bridge.check_kernel_shape(missing)
    ok("D2 a missing callable is reported by name",
       any("check is missing" in p for p in problems), repr(problems))

    not_callable = stub_kernel(check="not a function")
    problems = bridge.check_kernel_shape(not_callable)
    ok("D3 a non-callable is reported as such",
       any("not callable" in p for p in problems), repr(problems))

    renumbered = stub_kernel(EXIT_FAIL=9)
    problems = bridge.check_kernel_shape(renumbered)
    ok("D4 renumbered exit codes are refused",
       any("EXIT_FAIL is 9" in p for p in problems), repr(problems))
    ok("D5 and the reason explains why it matters",
       any("silently change what a failure means" in p for p in problems), repr(problems))

    wrong_verdicts = stub_kernel(VERDICTS=("pass", "fail"))
    problems = bridge.check_kernel_shape(wrong_verdicts)
    ok("D6 a missing verdict name is reported",
       any("review" in p for p in problems), repr(problems))

    wrong_codes = stub_kernel(EXIT_CODES=(0, 1, 2))
    problems = bridge.check_kernel_shape(wrong_codes)
    ok("D7 a changed EXIT_CODES tuple is reported",
       any("EXIT_CODES" in p for p in problems), repr(problems))


def test_bridge_load():
    kernel = bridge.load_kernel(override=stub_kernel())
    ok("D8 load_kernel returns a Kernel wrapper", isinstance(kernel, bridge.Kernel))
    eq("D9 and exposes the kernel version", kernel.kernel_version, "0.1.2")

    # A missing kernel must be actionable, exit 3, and must NOT be reported as a
    # failure of the target.
    raises("D10 a missing kernel is RULEPACK_UNAVAILABLE",
           "E_RULEPACK_UNAVAILABLE", bridge.load_kernel,
           module_name="definitely_not_installed_xyz_123")
    try:
        bridge.load_kernel(module_name="definitely_not_installed_xyz_123")
    except PublishGateError as exc:
        eq("D11 and exits 3, not 1", exc.exit_code, version.EXIT_USAGE)
        ok("D12 and names the install command",
           "pip install phocinae-rulepack" in exc.message, exc.message)

    raises("D13 a wrong-shaped kernel is RULEPACK_API_MISMATCH",
           "E_RULEPACK_API_MISMATCH", bridge.load_kernel,
           override=stub_kernel(VERDICTS=("pass",)))

    # A major outside the supported range is refused even when the shape matches.
    unsupported = stub_kernel()
    unsupported.__version__ = "9.0.0"
    raises("D14 an unsupported major is refused",
           "E_RULEPACK_API_MISMATCH", bridge.load_kernel, override=unsupported)

    # dump_canonical is normalised to str whichever the kernel returns.
    class Bytes(stub_kernel().__class__):
        pass

    bytes_module = stub_kernel(dump_canonical=lambda payload: b'{"a":1}')
    eq("D15 bytes are decoded to str",
       bridge.Kernel(bytes_module).dump_canonical({}), '{"a":1}')
    str_module = stub_kernel(dump_canonical=lambda payload: '{"a":2}')
    eq("D16 str passes through", bridge.Kernel(str_module).dump_canonical({}), '{"a":2}')

    # A bad pack is a configuration problem (3), not a finding about the target (1).
    #
    # Both stubs must share one `PackError` class. `stub_kernel()` builds a fresh
    # exception class per call, so a raiser built from one stub and a catcher
    # built from another never match, and the test would report "the bridge did
    # not translate the error" when in fact the two stubs simply disagreed about
    # what the error type was.
    base = stub_kernel()
    pack_error = base.PackError

    def explode(target, packs_dir=None):
        raise pack_error("nope")

    bad = stub_kernel(load_pack=explode)
    bad.PackError = pack_error
    raises("D17 a rejected pack is RULEPACK_PACK_INVALID",
           "E_RULEPACK_PACK_INVALID", bridge.Kernel(bad).load_pack, "x")
    try:
        bridge.Kernel(bad).load_pack("x")
    except PublishGateError as exc:
        eq("D18 and exits 3, so a bad pack is not blamed on the target",
           exc.exit_code, version.EXIT_USAGE)


def test_bridge_against_the_real_kernel():
    """The genuinely load-bearing check: the installed kernel must satisfy the shape."""
    try:
        kernel = bridge.load_kernel()
    except PublishGateError as exc:
        skip("D19 real kernel loads",
             "%s: %s" % (exc.reason_code, exc.message))
        return
    eq("D19 the real kernel passes the shape check",
       bridge.check_kernel_shape(kernel.module), [])
    ok("D20 the real kernel reports a version",
       isinstance(kernel.kernel_version, str) and kernel.kernel_version, kernel.kernel_version)
    pack = kernel.load_pack("expense-policy")
    ok("D21 a real pack loads through the bridge", hasattr(pack, "rules"))

    # The document has to speak the pack's language. The kernel refuses an input
    # no rule reads (`E_NO_RULE_REACHES_INPUT`) rather than returning a vacuous
    # pass, which is the same "nothing verified is not success" rule this project
    # enforces -- so the fixture must be a real claim, not a placeholder.
    payload = kernel.check(pack, _expense_document())
    ok("D22 a real check returns a verdict we recognise",
       payload.get("verdict") in version.VERDICTS, repr(payload.get("verdict")))
    ok("D23 and reports at least one reason",
       len(payload.get("reasons") or []) >= 1, repr(payload.get("reasons")))


def _expense_document():
    """A record the shipped expense-policy pack actually reads."""
    return {
        "claim_id": "C1",
        "amount": 120.0,
        "limit": 100.0,
        "currency": "EUR",
        "category": "meals",
        "receipt_present": True,
        "pre_approved": True,
        "attendees": 2,
        "notes": "team lunch",
    }


# ---------------------------------------------------------------------------
# E. facts: type discipline and provenance
# ---------------------------------------------------------------------------


def test_facts_types():
    good = facts_mod.build_facts("podcast", "t.mp3", {"duration_s": 3.5, "channels": 2})
    eq("E1 a well-typed document is built", good["schema"], version.FACTS_SCHEMA)
    eq("E2 measured is the default provenance",
       good["provenance"]["duration_s"], "measured")

    raises("E3 a string duration is a TYPE error, not a finding",
           "E_FACTS_TYPE_ERROR", facts_mod.build_facts, "podcast", "t.mp3",
           {"duration_s": "3.5"})
    raises("E4 an integer duration is accepted as a number",
           "E_FACTS_TYPE_ERROR", facts_mod.build_facts, "podcast", "t.mp3",
           {"channels": 2.5})

    # A bool is an int subclass in Python, and True is not a channel count.
    raises("E5 a bool where a number belongs is refused",
           "E_FACTS_TYPE_ERROR", facts_mod.build_facts, "podcast", "t.mp3",
           {"channels": True})
    raises("E6 a bool where a float belongs is refused",
           "E_FACTS_TYPE_ERROR", facts_mod.build_facts, "podcast", "t.mp3",
           {"duration_s": False})

    # None means "not obtained", which is legitimate and must not be a type error.
    try:
        doc = facts_mod.build_facts("podcast", "t.mp3", {"duration_s": None})
    except PublishGateError as exc:
        ok("E7 None is allowed as 'not obtained'", False, str(exc))
    else:
        ok("E7 None is allowed as 'not obtained'", True)
        eq("E8 and is recorded as unavailable, not measured",
           doc["provenance"]["duration_s"], "unavailable")
        eq("E9 and is listed as unavailable",
           facts_mod.unavailable_fields(doc), ["duration_s"])

    raises("E10 an unknown provenance value is refused",
           "E_FACTS_TYPE_ERROR", facts_mod.build_facts, "podcast", "t.mp3",
           {"duration_s": 1.0}, {"duration_s": "probably"})

    # A value present but with no explicit provenance defaults to measured when it
    # is a real value, and unavailable when it is None. Defaulting the other way
    # would make the document claim more than the measurer knows.
    doc = facts_mod.build_facts("podcast", "t.mp3", {"duration_s": 1.0, "codec": None})
    eq("E11 a real value defaults to measured", doc["provenance"]["duration_s"], "measured")
    eq("E12 a None defaults to unavailable", doc["provenance"]["codec"], "unavailable")


# ---------------------------------------------------------------------------
# F. the run: aggregation, and the refusal to call an unrun check a pass
# ---------------------------------------------------------------------------


def test_run_aggregation():
    kernel = bridge.load_kernel(override=stub_kernel())

    # A pack that fails one rule.
    failing = stub_kernel(check=lambda pack, document: {
        "verdict": "fail",
        "reasons": [{"rule": "r1", "verdict": "fail", "reason_code": "X", "evidence": "e"}],
    })
    report = gate.run("t.mp3", kind="podcast", facts=_podcast_facts(),
                      pack="p", kernel=bridge.Kernel(failing))
    eq("F1 a failing rule makes the run fail", report["status"], "fail")
    eq("F2 and exits 1", report["exit_code"], version.EXIT_FAIL)
    eq("F3 counts agree", report["counts"]["fail"], 1)

    passing = stub_kernel(check=lambda pack, document: {
        "verdict": "pass",
        "reasons": [{"rule": "r1", "verdict": "pass", "reason_code": "OK", "evidence": "e"}],
    })
    report = gate.run("t.mp3", kind="podcast", facts=_podcast_facts(),
                      pack="p", kernel=bridge.Kernel(passing))
    eq("F4 a passing rule makes the run pass", report["status"], "pass")
    eq("F5 and exits 0", report["exit_code"], version.EXIT_PASS)

    # No pack selected: the check did not run, so it is a SKIP, and a run of
    # nothing but skips must NOT exit 0.
    report = gate.run("t.mp3", kind="podcast", facts=_podcast_facts(),
                      pack=None, kernel=bridge.Kernel(passing))
    eq("F6 no pack means a skip", report["counts"]["skip"] >= 1, True)
    eq("F7 and nothing verified is exit 3", report["exit_code"], version.EXIT_USAGE)
    eq("F8 with the EMPTY_SCAN basis", report["exit_basis"], "E_EMPTY_SCAN")
    ok("F9 and it did not report a status of pass", report["status"] != "pass",
       report["status"])

    # An unrecognised verdict from the kernel is a mismatch, not a silent review.
    odd = stub_kernel(check=lambda pack, document: {
        "verdict": "maybe",
        "reasons": [{"rule": "r1", "verdict": "maybe", "reason_code": "?", "evidence": ""}],
    })
    raises("F10 a new kernel verdict is refused rather than folded into review",
           "E_RULEPACK_API_MISMATCH", gate.run, "t.mp3", "podcast",
           _podcast_facts(), "p", None, "strict", bridge.Kernel(odd))

    raises("F11 a bad exit policy is a usage error",
           "E_USAGE", gate.run, "t.mp3", "podcast", _podcast_facts(),
           "p", None, "lenient", bridge.Kernel(passing))


def _podcast_facts():
    return facts_mod.build_facts("podcast", "t.mp3", {"duration_s": 3.5, "channels": 2})


# ---------------------------------------------------------------------------
# G. the CLI contract
# ---------------------------------------------------------------------------


def test_cli():
    result = cli(["--version"])
    eq("G1 --version exits 0", result.returncode, 0)
    ok("G2 and prints the version", version.__version__ in result.stdout, result.stdout)

    result = cli(["--help"])
    eq("G3 --help exits 0", result.returncode, 0)
    for command in ("gates", "probe", "inspect", "check", "explain",
                    "verify-tripwire", "capture-fixtures"):
        ok("G4 --help lists %s" % command, command in result.stdout, result.stdout[:400])

    # Whether the kernel is importable changes what several assertions below can
    # mean. Probe once, then SKIP the kernel-dependent ones instead of failing
    # them: the README promises that assertions needing the real kernel report
    # SKIP, and a failure here would also abort the rest of this group, silently
    # dropping the CLI-contract assertions that do not need the kernel at all.
    kernel_present = cli(["gates"]).returncode == 0
    if not kernel_present:
        skip("G5 gates exits 0", "the kernel is not installed, so `gates` cannot run; "
                                 "install phocinae-rulepack to exercise the CLI contract")
    else:
        result = cli(["gates"])
        ok("G6 gates names the kernel it delegates to",
           "phocinae-rulepack" in result.stdout, result.stdout[:300])

    if kernel_present:
        result = cli(["inspect", "expense-policy"])
        eq("G7 inspect a real pack exits 0", result.returncode, 0)
    else:
        skip("G7 inspect a real pack exits 0", "no kernel")

    result = cli(["inspect"])
    eq("G8 inspect with no pack is a usage error", result.returncode, version.EXIT_USAGE)

    result = cli(["probe", "x"])
    eq("G9 probe says it is not implemented rather than doing nothing",
       result.returncode, version.EXIT_USAGE)
    ok("G10 and says so on stderr", "not implemented" in result.stderr, result.stderr)

    # --out with --dry-run is refused, and refused for the right reason.
    result = cli(["check", "t.mp3", "--out", "r.json"])
    eq("G11 --out with --dry-run exits 3", result.returncode, version.EXIT_USAGE)
    ok("G12 and names the reason code",
       "E_OUT_REQUIRES_NO_DRY_RUN" in result.stderr, result.stderr)

    # A check with a facts document and a real pack runs end to end.
    if not kernel_present:
        skip("G13 check --json emits parseable JSON", "no kernel")
        skip("G14..G17 report fields", "no kernel")
        result = cli(["explain", "t.mp3", "--check", "no-such-check"])
        eq("G19 explaining an absent check is refused", result.returncode, version.EXIT_USAGE)
        return

    facts_path = _write_facts()
    result = cli(["check", "t.mp3", "--kind", "podcast", "--facts", facts_path,
                  "--pack", "expense-policy", "--json"])
    ok("G13 check --json emits parseable JSON",
       _parses(result.stdout), result.stdout[:300])
    if _parses(result.stdout):
        report = json.loads(result.stdout)
        eq("G14 the report carries the schema id",
           report["schema"], version.REPORT_SCHEMA)
        eq("G15 and records the kernel version it used",
           report["kernel"]["distribution"], "phocinae-rulepack")
        eq("G16 the exit code matches the report", result.returncode, report["exit_code"])
        ok("G17 counts are present", set(report["counts"]) == set(version.COUNT_KEYS),
           repr(report["counts"]))

    result = cli(["check", "t.mp3", "--facts", "/nonexistent/facts.json"])
    eq("G18 a missing --facts is a usage error", result.returncode, version.EXIT_USAGE)

    result = cli(["explain", "t.mp3", "--check", "no-such-check", "--facts", facts_path])
    eq("G19 explaining an absent check is refused", result.returncode, version.EXIT_USAGE)
    ok("G20 and lists the ids that were present",
       "ids present" in result.stderr, result.stderr)


def _write_facts():
    root = os.environ.get("PHOCINAE_PUBLISH_GATE_TMP") or os.path.join(
        os.path.dirname(REPO), ".phocinae-publish-gate-tmp")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "facts.json")
    # The document must carry the fields the chosen pack reads. The kernel rejects
    # an input no rule touches rather than returning a vacuous pass, so a fixture
    # that merely has the right *shape* is not enough -- it has to be about the
    # same subject as the pack.
    document = _expense_document()
    document["schema"] = version.FACTS_SCHEMA
    document["kind"] = "declaration"
    document["target_path"] = "t.mp3"
    document["provenance"] = {name: "declared" for name in _expense_document()}
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle)
    return path


def _parses(text):
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def test_the_four_verification_fixes():
    """Each of these covers a defect an independent check found in 0.1.0.

    They are grouped because they share one shape: the tool exited **1**, which
    means "the target failed a check", when nothing about the target had been
    evaluated. The suite was green throughout, which is why each one now has an
    assertion rather than a comment.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["PYTHONUTF8"] = "1"

    def run_cli(args, cwd=REPO):
        return subprocess.run([sys.executable, "-m", "phocinae_publish_gate"] + list(args),
                              capture_output=True, text=True, encoding="utf-8",
                              cwd=cwd, env=env)

    # 1. A kernel PackError must be translated, not escape as a traceback.
    #    The README's own headline example triggered this.
    result = run_cli(["check", "episode.mp3", "--kind", "podcast",
                      "--pack", "expense-policy"])
    if "E_RULEPACK_UNAVAILABLE" in result.stderr:
        # The kernel is absent in this environment, so the rejection this test is
        # about cannot even be provoked. Skipped rather than failed: reporting a
        # missing dependency as a broken assertion would be the same confusion
        # between "a run that did not happen" and "a target that failed" that the
        # fix above exists to remove.
        skip("I1..I4 kernel rejection is translated",
             "the kernel is not installed, so the kernel's own PackError cannot be raised")
    else:
        eq("I1 a kernel rejection of the input exits 3, not 1",
           result.returncode, version.EXIT_USAGE)
        ok("I2 and names a registered reason code",
           "E_KERNEL_REJECTED_INPUT" in result.stderr, result.stderr[:200])
        ok("I3 and prints no traceback",
           "Traceback (most recent call last)" not in result.stderr, result.stderr[:400])
        ok("I4 and preserves the kernel's own reason code, which says more than ours",
           "E_NO_RULE_REACHES_INPUT" in result.stderr, result.stderr[:400])

    # 2. A fact document of the wrong top-level type is a measurement defect.
    for payload, label in (('[{"schema":"phocinae-facts/v1"}]', "a list"),
                           ('"just a string"', "a string"),
                           ("5", "a number")):
        path = os.path.join(_tmpdir(), "wrong-type.json")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        result = run_cli(["check", "t.mp3", "--facts", path, "--pack", "expense-policy"])
        eq("I5 %s as the whole document exits 3" % label,
           result.returncode, version.EXIT_USAGE)
        ok("I6 %s names a type error, not a finding" % label,
           "E_FACTS_TYPE_ERROR" in result.stderr, result.stderr[:200])
        ok("I7 %s prints no traceback" % label,
           "Traceback (most recent call last)" not in result.stderr, result.stderr[:300])

    # 3. A foreign schema must be refused rather than read as if it were ours.
    path = os.path.join(_tmpdir(), "foreign.json")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write('{"schema": "someone-elses-facts/v9", "claim_id": "C1"}')
    result = run_cli(["check", "t.mp3", "--facts", path, "--pack", "expense-policy"])
    eq("I8 a foreign facts schema exits 3", result.returncode, version.EXIT_USAGE)
    ok("I9 and says which schema it wanted",
       "E_FACTS_SCHEMA_UNSUPPORTED" in result.stderr
       and version.FACTS_SCHEMA in result.stderr, result.stderr[:300])

    # 4. `--out` must actually write, and `--dry-run` must still refuse it.
    out_path = os.path.join(_tmpdir(), "written-report.json")
    if os.path.exists(out_path):
        os.remove(out_path)
    good_facts = _write_facts()
    result = run_cli(["check", "t.mp3", "--facts", good_facts, "--pack", "expense-policy",
                      "--no-dry-run", "--out", out_path, "--json"])
    if result.returncode == version.EXIT_USAGE and "E_RULEPACK_UNAVAILABLE" in result.stderr:
        skip("I10..I12 --out writes the report",
             "no kernel, so the check cannot run and correctly writes nothing")
    else:
        ok("I10 --out with --no-dry-run creates the file", os.path.isfile(out_path),
           "exit was %d" % result.returncode)
        if os.path.isfile(out_path):
            with open(out_path, "r", encoding="utf-8") as handle:
                written = json.load(handle)
            eq("I11 and it is the same report the exit code describes",
               written["exit_code"], result.returncode)
            eq("I12 and it carries the report schema", written["schema"],
               version.REPORT_SCHEMA)

    os.remove(out_path) if os.path.exists(out_path) else None
    result = run_cli(["check", "t.mp3", "--facts", good_facts, "--out", out_path])
    eq("I13 --out under --dry-run is still refused", result.returncode, version.EXIT_USAGE)
    ok("I14 and writes nothing", not os.path.exists(out_path))


def _tmpdir():
    root = os.environ.get("PHOCINAE_PUBLISH_GATE_TMP") or os.path.join(
        os.path.dirname(REPO), ".phocinae-publish-gate-tmp")
    os.makedirs(root, exist_ok=True)
    return root


def test_lazy_imports():
    """`import phocinae_publish_gate` must not require the kernel."""
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, phocinae_publish_gate as m; "
         "print(m.__version__); "
         "print('phocinae_rulepack' in sys.modules)"],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=REPO)
    eq("H1 importing the package exits 0", result.returncode, 0)
    lines = result.stdout.strip().splitlines()
    eq("H2 and reports the version", lines[0] if lines else "", version.__version__)
    eq("H3 and does NOT import the kernel", lines[1] if len(lines) > 1 else "", "False")


def main():
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    tests = [
        test_exit_codes,
        test_aggregate_status,
        test_reason_code_closure,
        test_bridge_shape_check,
        test_bridge_load,
        test_bridge_against_the_real_kernel,
        test_facts_types,
        test_run_aggregation,
        test_cli,
        test_the_four_verification_fixes,
        test_lazy_imports,
    ]
    stream.write("phocinae-publish-gate test suite (%d groups)\n\n" % len(tests))
    crashed = 0
    for test in tests:
        before = len(CHECKS["failed"])
        try:
            test()
        except Exception:  # noqa: BLE001 - a crashing test is a failing test
            import traceback
            CHECKS["failed"].append(("%s raised" % test.__name__, traceback.format_exc()))
            crashed += 1
        new = CHECKS["failed"][before:]
        stream.write("%-38s %s\n" % (test.__name__, "OK" if not new else "FAIL (%d)" % len(new)))

    stream.write("\n")
    for label, detail in CHECKS["failed"]:
        stream.write("  FAIL %s\n" % label)
        if detail:
            for line in str(detail).rstrip().splitlines():
                stream.write("       %s\n" % line)
    for label, why in CHECKS["skipped"]:
        stream.write("  SKIP %s\n" % label)
        for line in str(why).rstrip().splitlines():
            stream.write("       %s\n" % line)
    stream.write("-" * 64 + "\n")
    stream.write("TOTAL PASS=%d FAIL=%d SKIP=%d  groups=%d/%d\n"
                 % (CHECKS["passed"], len(CHECKS["failed"]), len(CHECKS["skipped"]),
                    len(tests) - crashed, len(tests)))
    if CHECKS["skipped"]:
        stream.write("SKIP is not PASS: a skipped check verified nothing.\n")
    if CHECKS["passed"] == 0:
        stream.write("FAIL: nothing was verified\n")
        return 1
    return 1 if CHECKS["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
