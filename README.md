# Assembly Convention Checker

A source-level System V AMD64 ABI checker for learners writing GNU assembly in
AT&T syntax. It runs on Windows and Linux with Python 3.11 or newer and has no
runtime dependencies. Submitted assembly is never assembled or executed.

The checker follows branches and loops and tracks register values, stack slots,
stack alignment, and the direction flag. It reports definite violations,
possible violations, and incomplete analysis separately. A clean report means
**“No issues found in the supported checks,”** not full ABI certification.

## Quick start

Run directly from this directory:

```console
python -m assembly_convention_checker examples/good.s --strict
python -m assembly_convention_checker examples/broken.s
python -m assembly_convention_checker examples/broken.s --format json
```

Install the CLI in a virtual environment:

```console
python -m venv .venv
```

On Windows:

```powershell
.venv\Scripts\python -m pip install .
.venv\Scripts\abi-check examples/good.s --strict
```

On Linux:

```console
.venv/bin/python -m pip install .
.venv/bin/abi-check examples/good.s --strict
```

Multiple files and standard input are supported. Use `--entry` for bare snippets
or to select specific functions; repeat the option to select more than one.
Entry selections apply to each input file, and requested labels must exist there.

```console
abi-check first.s second.s --strict
abi-check snippet.s --entry add_numbers
abi-check - --format json
```

For example, this call is misaligned because an ordinary function starts with
`%rsp modulo 16 = 8`:

```asm
.globl example
.type example,@function
example:
    call other_function
    ret
.size example,.-example
```

Allocate eight bytes before the call and release them afterward, accounting for
any other pushes or local allocation. The diagnostic includes the offending
source location, related stack operations, and a correction suggestion.

## Reading text reports

Each file starts with severity counts. Findings use compiler-style blocks with
a stable rule ID, a short title, a navigable location, and numbered source lines:

```text
examples/broken.s
3 errors | 0 warnings | 0 analysis gaps

error[ABI_STACK_ALIGNMENT]: Stack misaligned at transfer
  --> examples/broken.s:7:5
   |
 6 |     movq $42,%rbx
 7 |     call helper@PLT
   |     ^^^^
 8 |     std
   |
   = function: example
   = note: %rsp modulo 16 is 8; this call requires 0.
   = help: Account for the return address and all pushes when adjusting the stack.
```

`note:` contains the full explanation; `help:` suggests a correction. Related
operations have dashed underlines in the same excerpt, with `...` between distant
source ranges. References to other files remain explicit location notes.
An `ANALYSIS_UNKNOWN` gap accompanying a warning at the same location appears as
an analysis note instead of repeating the excerpt; it still counts as a gap.

The footer summarizes analyzed/reachable instruction counts and lists incomplete
functions with their reasons. Inferred function boundaries are also identified.
Errors can have complete coverage: completeness describes how much was analyzed,
not whether the code complies with the checked obligations.

```console
abi-check examples/broken.s --color never
abi-check examples/broken.s --color always
```

`--color auto` is the default: bold headings and standard severity colors appear
only when standard output is a terminal, unless `NO_COLOR` is set or `TERM=dumb`.
Explicit `always` and `never` override automatic detection. All severities also
have text labels. Redirected text and JSON have no ANSI styling by default;
JSON is unaffected even by `--color always`.

Prose wraps to the terminal width, capped at 100 columns, with an 88-column
fallback for redirected output. Source lines remain intact, tabs expand at
four-column stops, and terminal control characters are escaped. Unavailable
source locations are reported without an excerpt.

Python callers can use `render_text(report, source, *, width=88, color=False)`
from `assembly_convention_checker.cli`; the existing two-argument call remains
supported. Rendering does not alter report data or exit policies.

## What it checks

| Rule ID | Obligation |
| --- | --- |
| `ABI_STACK_ALIGNMENT` | `%rsp modulo 16 = 0` before calls; entry-style alignment before tails. |
| `ABI_STACK_RESTORE` | Restore the entry stack pointer before returns and tail transfers. |
| `ABI_RETURN_ADDRESS` | Preserve the entry return-address slot. |
| `ABI_CALLEE_SAVED` | Preserve `%rbx`, `%rbp`, and `%r12`–`%r15`. |
| `ABI_DIRECTION_FLAG` | DF is clear at calls, returns, and tail transfers. |
| `ABI_RED_ZONE_BOUNDS` | Stack accesses stay within allocated storage or the 128-byte red zone. |
| `ABI_RED_ZONE_LIVE` | Do not rely on data below the call-time stack pointer after a call. |
| `ABI_RET_CLEANUP` | Do not use nonzero immediate cleanup on `ret`. |

These rules are grounded in the [AMD64 psABI](https://gitlab.com/x86-psABIs/x86-64-ABI/-/raw/master/x86-64-ABI/low-level-sys-info.tex).
The checker assumes ordinary returning callees obey that convention. Each
discovered function is analyzed independently; its body's violations do not
change the abstract call summary used by its callers.

Text output summarizes coverage and identifies incomplete functions and inferred
boundaries. `complete` describes analysis coverage, not the absence of diagnostics.

| Category | Interpretation |
| --- | --- |
| `error` | Known state violates an obligation at the reported site. |
| `warning` | A violation is possible; inspect the explanation and related operations. |
| `analysis_gap` | A construct or unknown fact prevents completing analysis. |

Exit status is `0` when there are no errors, `1` for ABI errors, and `2` for
malformed input, invocation errors, or I/O failures. `--strict` also returns `1`
for warnings and gaps. Default status `0` can therefore accompany incomplete
coverage; always read the report or use strict mode in automation.

## Python API and JSON

```python
from assembly_convention_checker import analyze

report = analyze(source, filename="example.s", entries=("example",))
for diagnostic in report.diagnostics:
    print(diagnostic.rule_id, diagnostic.category, diagnostic.location)
payload = report.to_dict()
```

`entries=()` analyzes all identified functions. Reports expose `diagnostics`,
`functions`, `input_errors`, `complete`, and `schema_version`. Locations contain
`filename`, one-based `line`, and one-based `column`. Diagnostics also expose
`function`, `message`, `suggestion`, and `related_locations`.

CLI JSON has this envelope, including for a single file:

```json
{
  "schema_version": 1,
  "reports": [
    {
      "filename": "example.s",
      "schema_version": 1,
      "complete": false,
      "input_errors": false,
      "diagnostics": [],
      "functions": []
    }
  ]
}
```

The `functions` array in an actual complete report is nonempty. Each item includes
the name, aliases, entry location, `boundaries_inferred`, total/reachable/analyzed
instruction counts, `complete`, and `incomplete_checks`. Diagnostics are sorted
deterministically. The Python report and CLI JSON use the same diagnostic data.

## Supported source and limits

The target is Linux/ELF 64-bit AT&T assembly, irrespective of the host OS.
Both `.s` and already-expanded `.S` files can be read; suffixes do not trigger
preprocessing. See [SUPPORTED.md](SUPPORTED.md) for exact syntax and instruction
families, and [DESIGN.md](DESIGN.md) for the analysis model.

Function-signature checking, argument and return-value classification, variadic
calls, floating-point control state, unwinding, kernel/syscall conventions, and
custom calling conventions are outside this release. The checker does not
validate all instruction encodings or general memory safety.

Unresolved writes may alias tracked saves. Passing stack addresses to calls can
also expose those saves. These cases deliberately produce uncertainty. Joins
discard differing values; predicate relationships and complex reversible
arithmetic are not solved. Valid code can consequently require manual review.

## Tests

```console
python -m unittest discover -v
```

Tests include source fixtures with expected rule IDs/categories/locations,
Python API and CLI checks, and Linux x86-64 integration checks. The Linux suite
requires ELF-targeting GNU GCC/binutils; it assembles and executes only trusted,
checked-in fixtures. It checks register sentinels, call alignment, DF, and actual
red-zone clobbering. Other hosts skip those two integration tests.

The GitHub Actions workflow runs Python 3.11 and 3.13 on Windows and Ubuntu and
checks the installed `abi-check` command. No third-party test framework is needed.
