# -*- coding: utf-8 -*-
"""Stable vocabulary: versions, reason codes, verdicts, exit codes.

Every reason code this project can emit is listed here and nowhere else. The
design makes that a closed set with a test that fails on an unregistered value,
because an unregistered reason code is a code that no reader has a definition
for -- and a report full of those is a report nobody can act on.
"""

from __future__ import annotations

__version__ = "0.1.0"

#: The schema id of the fact documents this project produces.
FACTS_SCHEMA = "phocinae-facts/v1"

#: The schema id of the report this project emits.
REPORT_SCHEMA = "phocinae-publish-gate/report/v1"

#: The kernel this project delegates its verdict engine to.
KERNEL_DISTRIBUTION = "phocinae-rulepack"

#: The kernel majors this project has been written against.
#:
#: A range rather than a pin: the coupling is the public API shape (checked by
#: :func:`phocinae_publish_gate.bridge.load_kernel`), not the exact release. An
#: exact pin would need a release here for every kernel patch; no pin at all
#: would let a kernel major with a changed envelope be used silently.
KERNEL_SUPPORTED_MAJOR = (0,)

#: Verdicts, in increasing severity. The order is the aggregation rule.
VERDICTS = ("pass", "review", "fail")

#: Exit codes. These mirror the kernel's own contract rather than inventing a
#: fourth scheme: the design fixes 0/1/2/3 with the same meanings, so a caller
#: who already reads the kernel reads this the same way.
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_REVIEW = 2
EXIT_USAGE = 3

#: Exactly the exit codes the kernel defines. The bridge asserts the installed
#: kernel agrees, because this project's codes are defined *as* the kernel's: if
#: the kernel ever renumbered them, "1 means a failure" here would quietly stop
#: matching what a caller reading the kernel expects.
EXIT_CODES_EXPECTED = (0, 1, 2, 3)

#: Statuses a single check can have. `skip` is deliberately not a verdict: a
#: check that did not run is not a check that passed, and the two are counted in
#: separate buckets everywhere in this project.
STATUSES = ("pass", "review", "fail", "skip")

#: Reason codes. The **closed set**: `errors.py` refuses to construct a
#: PhocinaeError with a code that is not a key here, and a test asserts that every
#: code this package can raise is present.
REASON_CODES = {
    # -- usage and configuration ------------------------------------------
    "E_USAGE": "the command line could not be understood",
    "E_INPUT_NOT_FOUND": "a required input path does not exist",
    "E_OUT_REQUIRES_NO_DRY_RUN": "`--out` was given together with `--dry-run`; "
                                "writing a report is a filesystem write, and dry-run "
                                "means no writes",
    "E_PROFILE_KEY_MISSING": "a profile does not define a key a rule needs",
    "E_BAD_PROFILE": "a profile could not be parsed or is not well formed",
    # -- the kernel, which this project delegates its verdicts to ----------
    "E_RULEPACK_UNAVAILABLE": "the phocinae-rulepack kernel could not be imported",
    "E_RULEPACK_API_MISMATCH": "phocinae-rulepack is importable but its public API is "
                               "not the shape this project is written against",
    "E_RULEPACK_PACK_INVALID": "the kernel rejected a rule pack",
    # -- measurement and facts --------------------------------------------
    "E_FACTS_TYPE_ERROR": "a fact has the wrong type; a type error is not a finding "
                          "about the target and must not be reported as one",
    "E_UNSUPPORTED_KIND": "the target kind could not be determined or is not supported",
    # -- scan shape --------------------------------------------------------
    "E_EMPTY_SCAN": "no check produced a verdict, so nothing was verified",
    "E_VACUOUS_PASS_DETECTED": "the run would have exited 0 while nothing was "
                               "verified; this is an internal invariant, and hitting "
                               "it means a guard failed rather than that the target "
                               "is good",
    # -- checks that could not run -----------------------------------------
    #
    # These are skip reason codes, and they live in the same closed set as the
    # error codes on purpose. A skip that carries an unregistered string is a
    # skip whose reason no reader can look up -- and since "SKIP is not PASS" is
    # a rule this project enforces, the reason a check did not run is as much a
    # part of the output as the reason one failed.
    "SKIP_NO_MEASUREMENT_BACKEND": "this release ships no measurement backend, so no "
                                   "fact was measured and no rule could be evaluated",
    "SKIP_NO_PACK_SELECTED": "no rule pack was selected for this run",
    "SKIP_TOOL_UNAVAILABLE": "a tool the check needs (ffprobe/ffmpeg) is not "
                             "available in this environment",
    "SKIP_INPUT_ABSENT": "an optional input the check needs is not present",
    "SKIP_PROFILE_PARTIAL": "the check needs a full-length measurement and a partial "
                            "one was requested",
    # -- the write tripwire ------------------------------------------------
    "E_TRIPWIRE_TRIPPED": "the write tripwire recorded a write during a dry run",
    "E_TARGET_WRITE_BLOCKED": "a write to the target directory was attempted and "
                              "refused",
}

#: Statuses as the report's `counts` object names them.
COUNT_KEYS = ("pass", "review", "fail", "skip")


def aggregate_status(statuses):
    """Collapse a sequence of check statuses into one verdict.

    The rule is short and total:

    * any ``fail`` -> ``fail``
    * else any ``review`` -> ``review``
    * else any ``skip`` -> ``review`` (a check that did not run is not a pass,
      so it cannot yield a clean verdict; the report still lists it as ``skip``)
    * else ``pass``

    ``skip`` mapping to ``review`` rather than to its own verdict is the whole
    point of "SKIP is not PASS". The *status* stays ``skip`` -- the report must
    still say what did not run -- but the *run* cannot come out clean.

    **The empty sequence returns ``pass``, and that is a deliberate identity, not
    a claim.** Aggregation over nothing is the identity element, the same way
    ``sum([])`` is ``0``. It is safe here only because no caller may act on it:
    the sole caller is the report builder, which never has an empty check list,
    and the exit code does not come from this function at all -- it comes from
    :func:`exit_code_for`, which reads *counts* and returns exit 3 when the
    verdict buckets are all zero.

    Every intermediate value in this function was written to be safe rather than
    to be zero-safe: a defect that made it usable here would defeat the guard
    downstream.
    """
    seen = list(statuses)
    if "fail" in seen:
        return "fail"
    if "review" in seen:
        return "review"
    if "skip" in seen:
        return "review"
    return "pass"


def exit_code_for(counts, policy="strict", empty_reason=None):
    """Map counted statuses to an exit code.

    ``policy`` is ``strict`` (default) or ``content``. The two differ in exactly
    one way: under ``content`` a ``skip`` does not by itself lift the code to 2.
    It does **not** allow "nothing was verified" to become success -- see the
    hard guard below, which applies to both policies.
    """
    # An unrecognised key in `counts` means a status outside STATUSES was counted,
    # which is a broken invariant rather than a target that passed. Checked before
    # anything else, because every sum below assumes the keys mean what they say.
    unknown = [key for key in counts if key not in COUNT_KEYS]
    if unknown:
        return EXIT_USAGE, "E_VACUOUS_PASS_DETECTED"

    total_verdicts = counts.get("pass", 0) + counts.get("fail", 0) + counts.get("review", 0)

    # The hard guard, independent of policy.
    #
    # Under `content`, a run where every check skipped has pass=0, fail=0,
    # review=0 -- and a naive reading gives exit 0, recording "a check that did
    # nothing" as "success". That is the precise failure this project exists to
    # catch in others, so it cannot be the one it commits. Nothing was verified
    # is an empty scan, and an empty scan is exit 3 whatever the policy says.
    if total_verdicts == 0:
        return EXIT_USAGE, empty_reason or "E_EMPTY_SCAN"

    if counts.get("fail", 0) > 0:
        return EXIT_FAIL, "FAIL"
    if counts.get("review", 0) > 0:
        return EXIT_REVIEW, "REVIEW"
    if counts.get("skip", 0) > 0 and policy == "strict":
        return EXIT_REVIEW, "SKIP"
    if counts.get("pass", 0) > 0:
        return EXIT_PASS, "PASS"
    # Unreachable: total_verdicts > 0 and none of the three buckets is positive
    # would mean a status outside STATUSES was counted, which the check at the top
    # already caught. Kept as a belt-and-braces return rather than an assertion,
    # because a report that exits 0 by falling off the end of this function is the
    # exact outcome this project must never produce.
    return EXIT_USAGE, "E_VACUOUS_PASS_DETECTED"
