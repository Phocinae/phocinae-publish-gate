# -*- coding: utf-8 -*-
"""Fact documents -- what this project measures, and what the kernel judges.

The split is the design's central one, and it is worth restating because it is
what keeps the two projects from duplicating each other:

* **this project measures.** It runs `ffprobe`, reads manifests and sidecars, and
  writes down numbers with their provenance. It reaches no verdict.
* **the kernel judges.** It loads a rule pack, evaluates the rules against the
  facts, and returns a verdict.

The document produced here is `phocinae-facts/v1`. Every value carries how it was
obtained, because "0.0" from a measurement that could not run and "0.0" from a
file that really is silent are the same number and different facts. Collapsing
them is the failure this project exists to catch.

Type discipline
---------------

A fact of the wrong type is **not** a finding about the target. If a duration
arrives as the string ``"3.5"`` rather than the number ``3.5``, that is a defect
in whoever produced the document, and reporting it as a rule failure would blame
the podcast for a bug in the measurer. So type checking happens here, before the
kernel is called, and raises ``E_FACTS_TYPE_ERROR`` (exit 3) rather than
producing a verdict.
"""

from __future__ import annotations

from .errors import PublishGateError
from .version import EXIT_USAGE, FACTS_SCHEMA

#: How a value was obtained. The vocabulary is closed for the same reason the
#: reason codes are: a provenance value nobody can define is not provenance.
PROVENANCE = (
    "measured",      # read from the target itself by a tool
    "declared",      # asserted by a manifest or sidecar the target ships
    "derived",       # computed from other facts, and the formula is recorded
    "unavailable",   # could not be obtained; the value must be None, never 0
)

#: Field type expectations. `None` is always allowed for any field -- it means
#: "not obtained", which is a legitimate state that the kernel's rules treat as
#: missing rather than as a value. What is checked is that a *present* value has
#: the stated type.
_FIELD_TYPES = {
    "duration_s": float,
    "channels": int,
    "sample_rate_hz": int,
    "bit_rate_bps": int,
    "loudness_lufs": float,
    "true_peak_dbtp": float,
    "size_bytes": int,
    "subtitle_lines": int,
    "glossary_terms": int,
    "glossary_hits": int,
    "halfwidth_violations": int,
    "embedded_fonts": int,
    "declared_fonts": int,
    "content_hash": str,
    "container": str,
    "codec": str,
    "target_path": str,
    "kind": str,
}


def _describe(value):
    return "%s (%r)" % (type(value).__name__, value if len(repr(value)) <= 40 else repr(value)[:40] + "...")


def check_fact_types(facts):
    """Return a list of type problems in ``facts``. Empty means well-typed.

    Only values that are present and not ``None`` are checked. Booleans are
    rejected where a number is expected even though ``bool`` is a subclass of
    ``int`` in Python: ``True`` is not a duration, and accepting it would let a
    schema mistake through as a measurement.
    """
    problems = []
    for name, expected in _FIELD_TYPES.items():
        if name not in facts:
            continue
        value = facts[name]
        if value is None:
            continue
        if expected is float and isinstance(value, bool):
            problems.append("%s is a bool, expected a number" % name)
            continue
        if expected is int and isinstance(value, bool):
            problems.append("%s is a bool, expected an integer" % name)
            continue
        if expected is float:
            if not isinstance(value, (int, float)):
                problems.append("%s should be a number but is %s" % (name, _describe(value)))
        elif not isinstance(value, expected):
            problems.append("%s should be %s but is %s"
                            % (name, expected.__name__, _describe(value)))
    return problems


def build_facts(kind, target_path, values, provenance=None, notes=None):
    """Assemble a `phocinae-facts/v1` document.

    ``values`` is the measured mapping; ``provenance`` maps a field name to one
    of :data:`PROVENANCE`. A field with no recorded provenance is ``unavailable``
    rather than ``measured``: defaulting to the stronger claim would make the
    document assert more than the measurer knows, which is the one direction this
    must never fail in.
    """
    facts = {
        "schema": FACTS_SCHEMA,
        "kind": kind,
        "target_path": target_path,
        "provenance": {},
        "notes": list(notes or []),
    }
    supplied = dict(provenance or {})
    for name, value in values.items():
        facts[name] = value
        origin = supplied.get(name, "unavailable" if value is None else "measured")
        if origin not in PROVENANCE:
            raise PublishGateError(
                "E_FACTS_TYPE_ERROR",
                "field %r was given provenance %r, which is not one of %s"
                % (name, origin, ", ".join(PROVENANCE)),
                EXIT_USAGE,
            )
        facts["provenance"][name] = origin

    problems = check_fact_types(facts)
    if problems:
        raise PublishGateError(
            "E_FACTS_TYPE_ERROR",
            "the fact document is not well typed, so no rule can be evaluated against "
            "it; this is a defect in the measurement, not a finding about the target",
            EXIT_USAGE,
            problems,
        )
    return facts


def unavailable_fields(facts):
    """Fields the document records as `unavailable`.

    Reported separately from failures so a reader can see the difference between
    "this was measured and is wrong" and "this could not be measured".
    """
    return sorted(name for name, origin in facts.get("provenance", {}).items()
                  if origin == "unavailable")
