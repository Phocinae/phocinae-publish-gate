# -*- coding: utf-8 -*-
"""The bridge to `phocinae-rulepack` -- the only place this project calls the kernel.

The design says this project does not reinvent the verdict engine: rule loading,
expression evaluation, the three-state output and the exit codes all come from
the kernel. That makes the kernel's **API shape** a real dependency, and this
module's job is to check it rather than to assume it.

Why check the shape and not just the version
--------------------------------------------

A version pin answers "is this a release I have seen before". It does not answer
"does this release still expose the names and semantics I call", and those are
different questions:

* a patch release can add, rename or re-type a public name without that being a
  breaking change by semver's rules as commonly practised;
* a checkout, a vendored copy or a fork can report any version at all;
* the failure without a shape check is not a clean error. It is an
  ``AttributeError`` raised somewhere inside a rule evaluation, or -- much worse
  -- a run that completes and reports verdicts computed against different
  semantics. The first sends the reader to the wrong repository; the second is
  silently wrong, which this project treats as the worse outcome everywhere else.

So the bridge imports the kernel, checks every name this project relies on, and
**refuses to run** if the shape is not what the rules below assume.
"""

from __future__ import annotations

import importlib

from .errors import kernel_api_mismatch, kernel_unavailable
from .version import EXIT_CODES_EXPECTED, KERNEL_DISTRIBUTION, KERNEL_SUPPORTED_MAJOR

#: The public names this project calls, and what each must be.
#:
#: ``callable`` entries are called; ``value`` entries are compared. Listing them
#: here rather than inline in the check keeps the dependency surface readable: a
#: reader can see the whole contract with this project in one screen.
_REQUIRED_CALLABLES = (
    # load_pack(target, packs_dir=None) -> Pack
    "load_pack",
    # check(pack, document) -> payload dict
    "check",
    # check_many(pack, documents) -> batch envelope
    "check_many",
    # exit_code(payload) -> int
    "exit_code",
    # dump_canonical(payload) -> str
    "dump_canonical",
)

_REQUIRED_VALUES = (
    # The exit-code contract this project's own codes mirror.
    "EXIT_PASS",
    "EXIT_FAIL",
    "EXIT_REVIEW",
    "EXIT_USAGE",
    # The three-state verdict vocabulary.
    "VERDICTS",
    # The exception type raised for a bad pack, which the bridge must translate.
    "PackError",
)


def check_kernel_shape(module):
    """Return a list of problems with ``module``'s public API.

    An empty list means the shape is as expected. Each problem is a sentence
    naming the name and what was wrong, because the caller turns this list into
    the detail of an ``E_RULEPACK_API_MISMATCH`` and the reader has to act on it.
    """
    problems = []

    for name in _REQUIRED_CALLABLES:
        attribute = getattr(module, name, None)
        if attribute is None:
            problems.append("%s is missing" % name)
        elif not callable(attribute):
            problems.append("%s is present but is %s, not callable"
                            % (name, type(attribute).__name__))

    for name in _REQUIRED_VALUES:
        if not hasattr(module, name):
            problems.append("%s is missing" % name)

    if not problems:
        expected = {"EXIT_PASS": 0, "EXIT_FAIL": 1, "EXIT_REVIEW": 2, "EXIT_USAGE": 3}
        for name, value in expected.items():
            actual = getattr(module, name)
            if actual != value:
                problems.append("%s is %r, expected %r -- this project maps its own exit "
                                "codes onto the kernel's, so a changed mapping would "
                                "silently change what a failure means"
                                % (name, actual, value))
        verdicts = getattr(module, "VERDICTS")
        try:
            missing = [v for v in ("pass", "review", "fail") if v not in verdicts]
        except TypeError:
            problems.append("VERDICTS is %s, which cannot be searched for the three "
                            "verdict names" % type(verdicts).__name__)
        else:
            if missing:
                problems.append("VERDICTS is missing %s" % ", ".join(missing))
        if tuple(getattr(module, "EXIT_CODES", ())) != EXIT_CODES_EXPECTED:
            problems.append("EXIT_CODES is %r, expected %r"
                            % (getattr(module, "EXIT_CODES", None), EXIT_CODES_EXPECTED))

    return problems


def load_kernel(module_name="phocinae_rulepack", override=None):
    """Import the kernel and verify its shape.

    ``override`` exists for tests: it lets a test hand in a stub module and check
    that a wrong shape is refused, without having to corrupt a real install.

    Raises :class:`~phocinae_publish_gate.errors.PublishGateError` rather than
    returning ``None``. A caller that forgets to check a return value would then
    proceed without an engine; a caller that forgets to catch an exception fails
    loudly, which is the correct default for "the engine is missing".
    """
    if override is not None:
        module = override
    else:
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise kernel_unavailable(
                "%s (%s)" % (exc, "install it with `pip install %s`" % KERNEL_DISTRIBUTION)
            ) from exc

    problems = check_kernel_shape(module)
    if problems:
        problems.append("kernel version reported as %r"
                        % getattr(module, "__version__", "unknown"))
        problems.append("this project was written against major version(s) %s"
                        % (", ".join(str(m) for m in KERNEL_SUPPORTED_MAJOR),))
        raise kernel_api_mismatch(problems)

    version = getattr(module, "__version__", "")
    try:
        major = int(str(version).split(".")[0])
    except (ValueError, IndexError):
        major = None
    if major is not None and major not in KERNEL_SUPPORTED_MAJOR:
        # The shape matched but the major is outside the supported range. Both
        # facts matter, so both are reported: a matching shape at an unsupported
        # major usually means the shape is about to change, not that it is safe.
        raise kernel_api_mismatch([
            "kernel version %r has major %d, outside the supported range %s"
            % (version, major, ", ".join(str(m) for m in KERNEL_SUPPORTED_MAJOR)),
            "the public API shape matched, so this is a policy refusal rather than a "
            "breakage: review what changed and widen KERNEL_SUPPORTED_MAJOR deliberately",
        ])

    return Kernel(module)


class Kernel:
    """A loaded kernel, wrapped so the rest of the project never imports it directly.

    Confining the dependency to one class means the "what do we call in the
    kernel" question has exactly one answer, and a test that stubs the kernel has
    exactly one seam.

    The version accessor is named :attr:`kernel_version`, not ``version``. That is
    not cosmetic: a ``version`` attribute on this class shadows the module's own
    ``__version__`` the moment anyone does ``kernel.module.version``, and the
    resulting ``AttributeError`` points at the kernel rather than at the wrapper.
    """

    def __init__(self, module):
        self._module = module

    @property
    def module(self):
        return self._module

    @property
    def kernel_version(self):
        return getattr(self._module, "__version__", "unknown")

    def _translate(self, exc, what, target):
        """Turn a kernel `PackError` into this project's error type.

        **This is the whole reason the exit codes mean anything.** The kernel
        raises `PackError` for things that are not findings about the target: an
        input document no rule reads (`E_NO_RULE_REACHES_INPUT`), an empty
        document (`E_EMPTY_SCAN`), a malformed pack. Those are *runs that did not
        happen*, and the kernel's own error module files them under exit 3.

        Without this translation they escaped as an uncaught `PackError`, so the
        process exited **1** with a Python traceback. `1` means "the target failed
        a check", so a broken input was being reported as a defective deliverable
        -- the exact confusion `errors.py` and the README say this project exists
        to prevent. It also made the README's own headline example fail.

        The kernel's reason code is preserved in the detail rather than dropped:
        `E_NO_RULE_REACHES_INPUT` says something this project's codes do not.
        """
        from .errors import PublishGateError
        from .version import EXIT_USAGE

        code = ""
        payload = getattr(exc, "args", ())
        if payload and isinstance(payload[0], str):
            code = payload[0]
        return PublishGateError(
            "E_KERNEL_REJECTED_INPUT",
            "the kernel refused to evaluate this input, so no check ran: %s" % (what,),
            EXIT_USAGE,
            [str(exc), "kernel reason code: %s" % (code or "(none)"),
             "target: %s" % (target,)],
        )

    def load_pack(self, target, packs_dir=None):
        """Load a pack, translating the kernel's pack error into this project's.

        The translation matters because the two mean different things to a
        caller: an invalid pack is a *configuration* problem here (exit 3), not a
        finding about the target (exit 1).
        """
        try:
            return self._module.load_pack(target, packs_dir=packs_dir)
        except self._module.PackError as exc:
            from .errors import PublishGateError
            from .version import EXIT_USAGE

            raise PublishGateError(
                "E_RULEPACK_PACK_INVALID",
                "the kernel rejected the rule pack %r" % (target,),
                EXIT_USAGE,
                [str(exc)],
            ) from exc

    def check(self, pack, document):
        try:
            return self._module.check(pack, document)
        except self._module.PackError as exc:
            raise self._translate(exc, "the fact document did not reach any rule",
                                  getattr(pack, "id", pack)) from exc

    def check_many(self, pack, documents):
        try:
            return self._module.check_many(pack, documents)
        except self._module.PackError as exc:
            raise self._translate(exc, "the ledger did not reach any rule",
                                  getattr(pack, "id", pack)) from exc

    def dump_canonical(self, payload):
        """Canonical JSON for ``payload``, always as ``str``.

        The kernel's function is typed as returning ``str`` in its own public
        name list, but the implementation returns ``bytes``. Rather than depend
        on which of those is true in a given release, this normalises: a caller
        that has to check the type before writing it is a caller who will forget
        to on some path. Verified in both directions by a test that hands in a
        bytes-returning stub and a str-returning one.
        """
        dumped = self._module.dump_canonical(payload)
        if isinstance(dumped, bytes):
            return dumped.decode("utf-8")
        return str(dumped)
