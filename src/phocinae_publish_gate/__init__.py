# -*- coding: utf-8 -*-
"""phocinae-publish-gate -- pre-publication checks for media deliverables.

The project does not own a verdict engine. It measures facts and hands them to
`phocinae-rulepack`, which evaluates the rules. That division is why this package
has an unusual dependency for its family: it is the first of them that *requires*
another package at run time, and the requirement is deliberate rather than
incidental. Reimplementing rule evaluation here would mean two engines whose
disagreements nobody could adjudicate.

The public surface is small on purpose:

``main``
    the CLI entry point.
``run``
    run one target and return the report dict.
``load_kernel``
    import and shape-check the engine, raising a registered error if it is not
    usable.
"""

from __future__ import annotations

from .version import (
    EXIT_CODES_EXPECTED,
    EXIT_FAIL,
    EXIT_PASS,
    EXIT_REVIEW,
    EXIT_USAGE,
    FACTS_SCHEMA,
    KERNEL_DISTRIBUTION,
    KERNEL_SUPPORTED_MAJOR,
    REASON_CODES,
    REPORT_SCHEMA,
    STATUSES,
    VERDICTS,
    __version__,
    aggregate_status,
    exit_code_for,
)

__all__ = [
    "EXIT_CODES_EXPECTED",
    "EXIT_FAIL",
    "EXIT_PASS",
    "EXIT_REVIEW",
    "EXIT_USAGE",
    "FACTS_SCHEMA",
    "KERNEL_DISTRIBUTION",
    "KERNEL_SUPPORTED_MAJOR",
    "REASON_CODES",
    "REPORT_SCHEMA",
    "STATUSES",
    "VERDICTS",
    "__version__",
    "aggregate_status",
    "exit_code_for",
    "main",
    "run",
]


def __getattr__(name):
    """Resolve the heavier names lazily.

    ``import phocinae_publish_gate`` must not import the kernel. Two reasons, and
    the second is the important one:

    * a caller who only wants ``__version__`` or the reason-code table should not
      pay for importing a rule engine;
    * the kernel may be absent. An eager import would turn
      ``import phocinae_publish_gate`` into an ``ImportError`` for a caller who
      never intended to run a check, which is both surprising and unhelpful --
      the actionable error is the one raised when a check is actually attempted.
    """
    if name == "main":
        from .cli import main
        return main
    if name == "run":
        from .gate import run
        return run
    if name == "load_kernel":
        from .bridge import load_kernel
        return load_kernel
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__():
    return sorted(__all__)
