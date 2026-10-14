# -*- coding: utf-8 -*-
"""The one exception type, and the closed set of reason codes.

Two rules, both enforced here rather than by convention:

1. **Every error carries a registered reason code.** A code that is not in
   :data:`~phocinae_publish_gate.version.REASON_CODES` raises immediately at
   construction. A typo in a reason code therefore fails a test instead of
   reaching a user as an undefined string they cannot look up.
2. **Every error carries an exit code.** The CLI never decides its own exit code
   at the point of failure; it uses the one the error was built with. That keeps
   the mapping from failure to exit code in one readable table.
"""

from __future__ import annotations

from .version import REASON_CODES, EXIT_USAGE


class PublishGateError(Exception):
    """A failure with a registered reason code and an exit code.

    The message is for a human; the reason code is for a program. Both are
    always present, because a report that says only "something went wrong" is
    not actionable and a bare code is not readable.
    """

    def __init__(self, reason_code, message, exit_code=EXIT_USAGE, detail=None):
        if reason_code not in REASON_CODES:
            raise ValueError(
                "unregistered reason code %r: add it to version.REASON_CODES with a "
                "description, because a code no reader can look up is not actionable"
                % (reason_code,)
            )
        super().__init__("%s: %s" % (reason_code, message))
        self.reason_code = reason_code
        self.message = message
        self.exit_code = exit_code
        self.detail = detail

    def as_dict(self):
        payload = {"reason_code": self.reason_code, "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


def usage(message, detail=None):
    return PublishGateError("E_USAGE", message, EXIT_USAGE, detail)


def kernel_unavailable(detail):
    """The kernel could not be imported.

    Kept separate from a general error because the remedy is specific and a
    reader needs to be told it: install the dependency. Exit 3, not 1 -- this is
    a run that could not happen, not a target that failed a check, and reporting
    it as a failure would blame the target for a missing dependency.
    """
    return PublishGateError(
        "E_RULEPACK_UNAVAILABLE",
        "the phocinae-rulepack kernel is required and could not be imported; "
        "install it with `pip install phocinae-rulepack` "
        "(it provides the rule engine this project delegates its verdicts to)",
        EXIT_USAGE,
        detail,
    )


def kernel_api_mismatch(problems):
    """The kernel imported, but its public API is not the expected shape.

    This is the failure mode that a version pin alone does not catch and that
    would otherwise be caught by a confusing ``AttributeError`` deep inside a
    rule evaluation, or worse, by silently wrong verdicts.
    """
    return PublishGateError(
        "E_RULEPACK_API_MISMATCH",
        "phocinae-rulepack is installed but its public API is not the shape this "
        "project is written against; refusing to run rather than produce verdicts "
        "whose meaning cannot be trusted",
        EXIT_USAGE,
        list(problems),
    )
