# phocinae-publish-gate

**Pre-publication checks for media deliverables.** It measures facts about a
target and hands them to the [`phocinae-rulepack`](https://pypi.org/project/phocinae-rulepack/)
engine, which decides. It never writes to the target, and it never reaches a
verdict on its own.

```
$ phocinae-publish-gate check episode.mp3 --facts episode.facts.json --pack expense-policy
target : episode.mp3 (declaration)
engine : phocinae-rulepack 0.1.2
counts : pass=0 fail=0 review=3 skip=0

  REVIEW     r1_no_receipt_over_500             missing:receipt_attached
  REVIEW     r6_prior_approval_missing          missing:prior_approval
  REVIEW     r7_category_not_in_policy          matched:r7_category_not_in_policy

status : REVIEW   exit 2 (REVIEW)
```

## What this is, and the one unusual thing about it

This is the first package in its family with a **run-time dependency**, and the
dependency is the point rather than an accident. The verdict engine lives in
`phocinae-rulepack`: rule loading, expression evaluation, the three-state output
and the exit-code contract all come from there. Implementing them again here would
mean two engines whose disagreements nobody could adjudicate.

So the split is:

| this project | the kernel |
|---|---|
| measures — runs tools, reads manifests and sidecars | loads rule packs |
| records how each value was obtained | evaluates expressions |
| writes a `phocinae-facts/v1` document | returns `pass` / `review` / `fail` |
| aggregates and reports | defines the exit-code numbers |

Because that boundary is a real dependency rather than a convention, the bridge
**checks its shape** instead of assuming it:

```
$ python -c "import phocinae_publish_gate"      # works with the kernel absent
$ phocinae-publish-gate check ...               # refuses to run without it
ERROR E_RULEPACK_UNAVAILABLE: the phocinae-rulepack kernel is required and could
      not be imported; install it with `pip install phocinae-rulepack`
```

A version pin answers *"is this a release I have seen before"*. It does not answer
*"does this release still expose the names and semantics I call"*, and those are
different questions: a patch release can rename a public name without that being a
breaking change by semver as commonly practised, and a checkout or a fork can
report any version at all. The failure without a shape check is not a clean error —
it is an `AttributeError` raised inside a rule evaluation, or, much worse, a run
that completes and reports verdicts computed against different semantics.

So the bridge verifies every name it calls, checks that the kernel's exit-code
numbers still match the ones this project mirrors, and **refuses to run** if they
do not (`E_RULEPACK_API_MISMATCH`). It also refuses a kernel whose major version is
outside the supported range even when the shape matches — a matching shape at an
unsupported major usually means the shape is about to change, not that it is safe.

## Exit codes

The numbers are the kernel's own, so a caller who already reads one reads the other
the same way.

| code | meaning |
|---|---|
| `0` | everything passed, with no skips and no reviews |
| `1` | at least one `fail` |
| `2` | no failures, but at least one `review` or `skip` |
| `3` | usage or configuration error, an empty scan, or an internal invariant broken |

### The guard that matters

`--exit-policy content` relaxes exactly one thing: a `skip` no longer lifts the code
to 2. It does **not** let "nothing was verified" become success.

A run in which every check skipped has `pass=0, fail=0, review=0`. A naive reading
of the policy gives exit `0`, recording a check that did nothing as a success — the
precise failure this project exists to catch in other people's tooling. So:

> `counts.pass + counts.fail + counts.review == 0` → always exit **3**,
> `E_EMPTY_SCAN`, under **both** policies.

`SKIP is not PASS` is enforced in the aggregation as well: a `skip` keeps its own
status in the report — a reader must be able to see what did not run — but it
cannot yield a clean verdict.

## Type errors are not findings

A fact of the wrong type is a defect in the measurement, not a property of the
target. If a duration arrives as the string `"3.5"` instead of the number `3.5`,
reporting that as a rule failure would blame the podcast for a bug in the
measurer. So facts are type-checked *before* the kernel is called, and a type error
is exit 3 (`E_FACTS_TYPE_ERROR`) rather than a verdict.

Two consequences worth stating:

* `True` is not a duration. Booleans are rejected where a number is expected even
  though `bool` is a subclass of `int` in Python.
* `None` means *not obtained*, which is legitimate, and is recorded as
  `provenance: unavailable` rather than as zero. `0.0` from a measurement that could
  not run and `0.0` from a file that really is silent are the same number and
  different facts; collapsing them is the thing this project exists to catch.

A field with no recorded provenance defaults to `unavailable`, never to `measured`.
Defaulting the other way would make the document claim more than the measurer knows.

## Commands

```
phocinae-publish-gate gates                      # gates, packs, reason codes
phocinae-publish-gate inspect <pack>             # a pack's rules and citations
phocinae-publish-gate check <target> [options]   # the main command
phocinae-publish-gate explain <target> --check <id>
phocinae-publish-gate probe | verify-tripwire | capture-fixtures
```

`check` takes `--facts <path>` to evaluate an existing `phocinae-facts/v1`
document instead of measuring. That is not only a test seam: it is how a caller
checks a target whose measurement happens elsewhere — a CI job that already ran
`ffprobe`, for instance — without this tool spawning anything.

`--out` together with `--dry-run` is refused with `E_OUT_REQUIRES_NO_DRY_RUN`.
Writing a report is a filesystem write, and dry-run means no writes; the two flags
are in genuine conflict, and silently preferring one would be the wrong kind of
helpful. `--dry-run` is the default.

There is deliberately **no** `--fix`, `--apply`, `--write-tags` or `--normalize`.
No mode of this tool modifies the target.

## Scope of this release

**The commands that need a measurement backend are not implemented, and they say
so.** `probe`, `verify-tripwire` and `capture-fixtures` exit 3 with a message
naming what is missing, rather than being absent from the command surface or
silently doing nothing. A subcommand that quietly no-ops is worse than one that
says it is missing.

`check` therefore reports `skip` for measurement (`SKIP_NO_MEASUREMENT_BACKEND`)
unless you pass `--facts`. With no pack selected it skips for that reason too
(`SKIP_NO_PACK_SELECTED`) — and a run of nothing but skips exits **3**, by the guard
above.

What *is* complete and tested: the kernel bridge and its shape check, the closed
reason-code set, the exit-code semantics and the empty-scan guard, the fact
document and its type discipline, the aggregation, and the CLI contract.

The design specifies four gate packs (`q7` manuscript, `q8` podcast, `q9` music,
`c6` declaration) with a total of 78 rules, and the measurement backends that feed
them. Neither is in this release. Shipping a small number of unverified rules would
be worse than shipping none: a rule whose basis nobody checked is decoration, and
this family's whole discipline is that a gate whose citation cannot be resolved is
not a gate.

## Environment

Python `>=3.10`. One run-time dependency, `phocinae-rulepack>=0.1.2,<0.2`. No
network access at run time.

## Testing

```sh
python tests/test_publish_gate.py
```

139 assertions across 11 groups. The ones that matter most are about the
integration, because that is the only place a mistake would be silent: a missing
kernel must be an actionable error rather than an `ImportError`; a kernel whose API
shape changed must be refused rather than used; and an exit code must never be `0`
when nothing was verified.

Several assertions run against the **real** installed kernel rather than a stub, and
report `SKIP` if it is absent — and a skip is not a pass.

## License

Apache-2.0.
