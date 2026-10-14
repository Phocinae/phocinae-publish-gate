# -*- coding: utf-8 -*-
"""The check runner: measure, judge through the kernel, aggregate, report.

Order of operations, and why it is this order:

1. **Load the kernel.** First, because everything downstream is meaningless
   without it and a missing engine should fail before any work is done rather
   than after.
2. **Measure.** Produce a fact document, or a list of checks that could not be
   measured. Measurement never raises for an unavailable tool; it records
   ``skip``.
3. **Type-check the facts.** A malformed document stops the run here, before the
   kernel is called, so a measurer's bug can never be reported as a finding about
   the target.
4. **Judge.** Hand the facts to the kernel's `check`, which applies the pack.
5. **Aggregate.** Combine the measured checks, the kernel's verdict, and the
   skips into one report, then derive the exit code with the guards in
   :mod:`~phocinae_publish_gate.version`.
"""

from __future__ import annotations

from .bridge import load_kernel
from .errors import PublishGateError
from .facts import build_facts, check_fact_types, unavailable_fields
from .version import (
    COUNT_KEYS,
    EXIT_USAGE,
    FACTS_SCHEMA,
    REPORT_SCHEMA,
    STATUSES,
    __version__,
    aggregate_status,
    exit_code_for,
)


def _blank_counts():
    return {key: 0 for key in COUNT_KEYS}


def _status_of_kernel_verdict(verdict):
    """Map the kernel's verdict onto this project's status vocabulary.

    The vocabularies are the same three words, and they are checked rather than
    assumed: if the kernel ever returned a fourth, silently folding it into
    ``review`` would hide a semantic change behind a cautious-looking default.
    """
    if verdict not in ("pass", "review", "fail"):
        raise PublishGateError(
            "E_RULEPACK_API_MISMATCH",
            "the kernel returned verdict %r, which is not one of pass/review/fail"
            % (verdict,),
            EXIT_USAGE,
            ["this project maps the kernel's verdicts directly onto its own statuses, "
             "so a new verdict value has to be handled deliberately rather than assumed"],
        )
    return verdict


def run_checks(kernel, pack_target, facts, gates=None, packs_dir=None):
    """Evaluate one fact document against one pack, and return the checks.

    Returns ``(checks, kernel_payload)``. Each check is
    ``{check_id, status, reason_code, detail, cite}``.
    """
    pack = kernel.load_pack(pack_target, packs_dir=packs_dir)
    payload = kernel.check(pack, facts)

    checks = []
    for entry in payload.get("reasons", []):
        status = _status_of_kernel_verdict(entry.get("verdict", "review"))
        checks.append({
            "check_id": entry.get("rule") or "(no rule)",
            "status": status,
            "reason_code": entry.get("reason_code"),
            "detail": entry.get("evidence") or "",
            "cite": list(entry.get("cite") or []),
        })
    return checks, payload


def run(target_path, kind="auto", facts=None, pack=None, packs_dir=None,
        policy="strict", kernel=None, notes=None):
    """Run one target end to end and return the report dict.

    ``facts`` may be supplied directly. That is not only a test seam: it is how a
    caller checks a target whose measurement happens elsewhere (a CI job that
    already ran `ffprobe`, for instance) without this tool spawning anything.
    """
    if policy not in ("strict", "content"):
        raise PublishGateError(
            "E_USAGE",
            "--exit-policy must be 'strict' or 'content', got %r" % (policy,),
            EXIT_USAGE,
        )

    # Order matters, and it is not the order one would write by instinct.
    #
    # The document is validated **before** the kernel is loaded, because the
    # defects they detect have different owners. A malformed fact document is a
    # defect in the measurement; a missing kernel is a defect in the environment.
    # Loading the kernel first means a caller who has both problems is told about
    # the environment and never hears about their own malformed document -- and,
    # worse, a caller who has *only* a malformed document in an environment where
    # the kernel happens to be absent is told to install a package, which will not
    # help.
    if isinstance(facts, (list, tuple, str, int, float, bool)):
        raise PublishGateError(
            "E_FACTS_TYPE_ERROR",
            "the fact document must be an object but is %s; this is a defect in the "
            "measurement, not a finding about the target" % type(facts).__name__,
            EXIT_USAGE,
            ["expected an object with at least a `schema` key, got %r"
             % (facts if isinstance(facts, (int, float, bool)) else type(facts).__name__)],
        )
    if facts is not None:
        declared = facts.get("schema") if isinstance(facts, dict) else None
        if declared is not None and declared != FACTS_SCHEMA:
            raise PublishGateError(
                "E_FACTS_SCHEMA_UNSUPPORTED",
                "the fact document declares schema %r; this project evaluates %r. Reading "
                "a foreign document as if it were ours would judge a target on fields "
                "whose meaning nobody here has defined."
                % (declared, FACTS_SCHEMA),
                EXIT_USAGE,
                ["pass a document with `\"schema\": \"%s\"`, or omit the key" % FACTS_SCHEMA],
            )
        problems = check_fact_types(facts)
        if problems:
            raise PublishGateError(
                "E_FACTS_TYPE_ERROR",
                "the fact document is not well typed; refusing to evaluate rules "
                "against it, because this is a defect in the measurement rather than "
                "a finding about the target",
                EXIT_USAGE,
                problems,
            )

    kernel = kernel or load_kernel()

    resolved_kind = kind
    skipped = []
    if facts is None:
        # This release measures nothing on its own: every gate it ships consumes a
        # fact document. Saying so is the honest outcome -- a check that did not
        # run is a skip, and skips are counted and reported rather than dropped.
        facts = build_facts(
            kind=resolved_kind if resolved_kind != "auto" else "unknown",
            target_path=target_path,
            values={},
            notes=list(notes or []) + [
                "no measurement backend is implemented in this release, so no fact "
                "was measured; every check is reported as skip",
            ],
        )
        skipped.append({
            "check_id": "measure",
            "status": "skip",
            "reason_code": "SKIP_NO_MEASUREMENT_BACKEND",
            "detail": "this release ships no measurement backend: supply a facts "
                      "document, or use the library with an explicit one",
        })
        resolved_kind = facts.get("kind", "unknown")

    facts = dict(facts)

    declared_schema = facts.get("schema")
    if declared_schema is not None and declared_schema != FACTS_SCHEMA:
        raise PublishGateError(
            "E_FACTS_SCHEMA_UNSUPPORTED",
            "the fact document declares schema %r; this project evaluates %r. Reading a "
            "foreign document as if it were ours would judge a target on fields whose "
            "meaning nobody here has defined." % (declared_schema, FACTS_SCHEMA),
            EXIT_USAGE,
            ["pass a document with `\"schema\": \"%s\"`, or omit the key" % FACTS_SCHEMA],
        )

    problems = check_fact_types(facts)
    if problems:
        raise PublishGateError(
            "E_FACTS_TYPE_ERROR",
            "the fact document is not well typed; refusing to evaluate rules against it",
            EXIT_USAGE,
            problems,
        )

    checks = []
    kernel_payload = None
    if pack is not None:
        checks, kernel_payload = run_checks(
            kernel, pack, facts, packs_dir=packs_dir)
    else:
        skipped.append({
            "check_id": "rulepack",
            "status": "skip",
            "reason_code": "SKIP_NO_PACK_SELECTED",
            "detail": "no rule pack was selected for this run",
        })

    checks = list(checks) + list(skipped)

    counts = _blank_counts()
    for check in checks:
        assert check["status"] in STATUSES, check
        counts[check["status"]] += 1

    # The hard guard, stated once here as well as in `exit_code_for`, because a
    # reader of the report should be able to see why the code is 3 without
    # reading the mapper.
    code, why = exit_code_for(counts, policy=policy)
    if code == EXIT_USAGE and why == "E_EMPTY_SCAN" and counts["skip"] > 0:
        why = "E_EMPTY_SCAN"

    status = aggregate_status([c["status"] for c in checks]) if checks else "review"

    report = {
        "schema": REPORT_SCHEMA,
        "tool_version": __version__,
        "kernel": {"distribution": "phocinae-rulepack", "version": kernel.kernel_version},
        "target": {
            "path": target_path,
            "kind": resolved_kind,
            "facts_schema": facts.get("schema"),
        },
        "counts": counts,
        "status": status,
        "exit_code": code,
        "exit_basis": why,
        "checks": checks,
        "unavailable_facts": unavailable_fields(facts),
        "facts": facts,
        "kernel_payload": kernel_payload,
    }
    return report
