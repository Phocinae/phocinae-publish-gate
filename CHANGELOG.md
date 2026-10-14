# Changelog

## 0.1.1 — 2026-10-14

Fixes from an independent verification pass over 0.1.0, which found 2 blockers and
10 major findings. Every one is fixed here, and every one has an assertion.

### Blocker — a kernel rejection was reported as a failing target

`Kernel.check` handed the kernel's `PackError` straight through. `bridge.load_pack`
had always translated it, but `check` did not, and `cli.main` only catches this
project's own error type — so the exception escaped as a Python traceback and the
process exited **1**.

`1` means "the target failed a check". So an input the kernel refused to evaluate
at all was being reported as a defective deliverable: the exact confusion
`errors.py` and the README say this project exists to prevent. It also meant the
README's own headline example, copied verbatim, produced a traceback.

`Kernel.check` and `Kernel.check_many` now translate, with a registered code
(`E_KERNEL_REJECTED_INPUT`) and the kernel's own reason code preserved in the
detail — `E_NO_RULE_REACHES_INPUT` says something this project's codes do not.

### Blocker — a malformed fact document was reported as a failing target

`check_fact_types` tests field types with `name not in facts`, which is silently
"no problems" for a list and a `TypeError` for an int. So a document of the wrong
top-level type produced an uncaught `AttributeError`, exit **1**, and a traceback.

A document of the wrong type is a defect in the measurement, which is exit 3. The
document is now validated before anything reads it.

**And the validation moved ahead of the kernel load**, which is not the order one
would write by instinct. The two defects have different owners: a malformed
document is the measurement's fault, a missing kernel is the environment's. Loading
the kernel first means a caller with a malformed document in an environment that
happens to lack the kernel is told to install a package, which will not help.

### Major — `--out` was accepted, promised, and never written

`_cmd_check` read `args.out` in exactly one place: to reject it under `--dry-run`.
Nothing ever wrote the file. The refusal message itself told the user to
"Pass `--no-dry-run` to write the report", which was not true.

Implemented, written by rename rather than truncate-and-write so an interrupted run
cannot leave a half-written report for a later step to read as a complete one — or,
worse, leave the *previous* run's file to be read as a current pass.

### Major — a foreign facts schema was read as if it were ours

`FACTS_SCHEMA` was recorded in the report and never compared against the document.
Any JSON at all was evaluated as `phocinae-facts/v1`, judging a target on fields
whose meaning nobody in this project has defined. Now refused
(`E_FACTS_SCHEMA_UNSUPPORTED`).

### Major — the suite was red without the kernel and aborted a whole group

`README` says assertions needing the real kernel "report `SKIP` if it is absent".
They did not: `test_cli` failed on `gates`, and the raised `CheckFailed` ended the
group, so 21 further CLI-contract assertions never ran.

That is not a corner case. `release.yml` itself names `--no-deps` installation as a
real user path. Now? With the kernel: `PASS=139 FAIL=0 SKIP=0`. Without it:
`PASS=118 FAIL=0 SKIP=7`, exit 0. A missing dependency is reported as a skip, not
as a broken assertion — the same distinction as the blocker above.

### Also fixed

* `exc.detail` was iterated as if it were always a list, so a string detail — which
  is what the two most actionable errors carry — printed one character per line.
  The remedy text was unreadable exactly where it mattered most.
* `E_NO_RULE_REACHES_INPUT` from the kernel reached the user only inside a
  traceback; it is now carried in the detail of a registered error.

### Tests added

`test_the_four_verification_fixes`, covering all four. The suite went from 118
assertions in 10 groups to 139 in 11.

### Still not in this release

Unchanged from 0.1.0: the four gate packs and the measurement backends. `probe`,
`verify-tripwire` and `capture-fixtures` exit 3 and say they are unimplemented.
## 0.1.0 — 2026-10-14

First release. Pre-publication checks for media deliverables, with every verdict
delegated to the `phocinae-rulepack` engine.

### What ships

* **The kernel bridge**, and the shape check that makes the dependency safe. This
  is the only place this project calls the kernel, and it verifies every name it
  calls plus the exit-code numbers it mirrors, refusing to run on a mismatch.
* **The exit-code contract**, including the empty-scan guard that no policy can
  bypass.
* **The fact document** and its type discipline.
* **The CLI**, with the full command surface from the design. The three
  subcommands that need a measurement backend exit 3 naming what is missing rather
  than being absent or silently no-opping.

### Defects found while building it

All three were found by tests or by running the tool, and all three are the same
shape: something that would have failed *silently*.

**`stub_kernel()` built a fresh exception class per call.** A test that raised
`PackError` from one stub and caught it via another's `PackError` never matched, so
the test reported "the bridge did not translate the error" when in fact the two
stubs disagreed about what the error type was. Worth recording because it is a
general trap: a fixture factory that constructs a *class* on each call cannot be
used on both sides of a `try`/`except`.

**A `version` property shadowed the kernel's own `__version__`.** The `Kernel`
wrapper exposed `version`, and `kernel.module.version` then resolved to the
property rather than the module's `__version__` attribute -- producing an
`AttributeError` that pointed at the kernel instead of at the wrapper. Renamed to
`kernel_version`.

**`dump_canonical` returns bytes, not `str`.** The kernel's own public name list
types it as `str`; the implementation returns `bytes`. The bridge now normalises
in both directions rather than depending on which is true in a given release,
because a caller who has to check the type before using it is a caller who will
forget on some path. Pinned by tests that hand in a bytes-returning stub and a
`str`-returning one.

### Decisions worth stating

* **`aggregate_status([])` returns `pass`, deliberately.** It is the identity
  element of the fold, the way `sum([])` is `0`. It is safe only because no caller
  may act on it, and that is now enforced rather than assumed: the exit code does
  not come from this function at all. `exit_code_for` reads *counts* and returns
  exit 3 when every verdict bucket is zero, and it returns exit 3 for an
  unrecognised status key as well. Both are asserted.
* **A type error is not a finding.** Facts are type-checked before the kernel is
  called, so a defect in the measurement can never be reported as a property of the
  target. `True` is not a duration; `None` means *not obtained* and is recorded as
  `unavailable` rather than as zero.
* **A field with no recorded provenance defaults to `unavailable`**, never to
  `measured`. Defaulting the other way would make the document claim more than the
  measurer knows, which is the one direction this must never fail in.
* **Skip reason codes live in the same closed set as error codes.** A skip whose
  reason nobody can look up is as unactionable as a failure's, and since "SKIP is
  not PASS" is enforced here, the reason a check did not run is part of the output.

### Not in this release

The four gate packs (`q7`, `q8`, `q9`, `c6`; 78 rules in the design) and the
measurement backends that feed them. The commands that need a backend exit 3 and
say so. Shipping a small number of unverified rules would be worse than shipping
none: a rule whose basis nobody checked is decoration, and this family's rule is
that a gate whose citation cannot be resolved is not a gate.
