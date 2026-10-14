# Changelog

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
