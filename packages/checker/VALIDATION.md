# Local validation

## Checker investigation, 2026-10-07

The original user programs are preserved unchanged under
`tests/fixtures/user_programs/`. They are analyzed as source, not executed.

| Program | Before | After |
| --- | --- | --- |
| `custom_printf.s` | Six character-literal input errors; no functions analyzed. | All six functions, all 241 reachable instructions, zero diagnostics. |
| Snake `main.s` | Includes blocked the whole file; no functions analyzed. | Eleven callable routines across three files, all 184 reachable instructions analyzed; five errors, five warnings, two uncertainty gaps. |

Fixed checker issues:

1. Character constants were rejected, and character punctuation could be mistaken
   for comments, statement separators, or operand delimiters.
2. Local includes prevented all analysis. Includes now share symbol/section
   context, preserve file locations, and report missing files and cycles.
3. Directly called helpers without function metadata were not discovered.
   Executable labels in function-pointer tables were missed too.
4. Branch blocks preceding entries or lying outside an inferred region were
   omitted. They are now followed and counted in coverage, including adjacent
   shared blocks at explicit size boundaries.
5. All syscalls stopped analysis. Linux read/write and process-exit syscalls now
   have bounded summaries; unknown numbers still produce a gap.
6. GNU's valid two-operand `div`/`idiv` spelling was rejected. Accumulator width
   and identity are validated. GNU `as` independently accepted the tested forms.
7. Global/RIP-relative stores unnecessarily invalidated stack saves.
8. Joining countdown iterations lost stack alignment and saved registers.
9. Variable-length digit extraction lost its stack depth. Bounded partitions and
   unsigned quotient bounds now preserve its balanced pushes and pops.
10. Negative index-register values were treated as huge positive stack offsets,
    missing red-zone violations.
11. The extension lacked file context for includes and assumed every finding
    belonged to the root buffer. It now handles unsaved includes, dependency
    refreshes, excerpts, Problems, and navigation, including Windows path casing.

Snake findings retained by the corrected checker:

- Misaligned calls at `main.s:116`, `main.s:184`, `main.s:189`, and
  `handle_movement.s:56`.
- `%rbx` is not preserved by `relocate_apple` (`main.s:194`).
- Preservation warnings for `%rbx`/`%r12` in `render_screen` and
  `handle_movement`. The latter also has an uncertain return-address check due
  to indexed writes whose bounds are unknown. These remain warnings/gaps rather
  than claims of proven memory corruption.

Regression tests also deliberately break printf's saves and loop cleanup to
ensure the fixes do not hide violations. The analysis still has documented limits:
unsupported syscalls, indirect jumps, unexpanded macros/conditionals, large or
unresolved dynamic loops, and ambiguous memory aliases require manual review.

Validation results:

- Windows: 84 Python tests run, 82 passed and two Linux-only tests skipped.
- Ubuntu WSL: all 84 Python tests passed, including GNU assembly/runtime checks.
- All 13 extension unit tests passed.
- VS Code host tests passed, including unsaved include changes, diagnostics on
  included files, and navigation to those files.
- Visual tests passed in dark/light/high-contrast themes and narrow/wide layouts;
  the included-source screenshot was also inspected.
- The rebuilt `dist/assembly-convention-checker-0.1.0.vsix` passed the isolated
  package smoke test outside the checkout without an installed Python checker.

## Previous validation

Validation completed on 2026-10-06.

| Environment | Result |
| --- | --- |
| Windows, Python 3.11.4 | 68 tests passed; 2 Linux-only integration tests skipped. |
| Ubuntu WSL, Python 3.10.12, x86-64 GNU GCC/binutils | All 70 tests passed, including assembly and runtime integration. |

The project declares Python 3.11+ support. WSL's installed Python 3.10 was used
for supplemental source/runtime validation. The checked-in GitHub Actions matrix
targets Python 3.11 and 3.13 on Windows and Ubuntu; it has not been run remotely.

Commands used for the source suites:

```console
python -B -m unittest discover -q
wsl -d Ubuntu --cd /mnt/c/Users/efeca/Desktop/AssemblyConventionChecker --exec python3 -B -m unittest discover -q
```

The runtime harness verifies saved-register sentinels, call-stack alignment,
clear DF, red-zone clobbering, and the effects of zero-count 32-bit shifts and
false 32-bit conditional moves. It executes only checked-in fixtures.

A project-local virtual environment was used to build
`dist/assembly_convention_checker-0.1.0-py3-none-any.whl` and install that wheel
without fetching dependencies. The installed CLI was checked from a temporary
directory outside the source checkout against the good, broken, and red-zone
examples, including strict exit policies and JSON output.

The compiler-style text renderer was inspected using all three example reports.
Renderer tests cover source ordering, shared uncertainty notes, related ranges,
cross-file references, missing source, I/O errors, invalid positions, terminal
control escaping, tab-expanded underlines, and prose widths of 40, 80, and 120
columns. Color tests cover terminal detection, redirected output, explicit
overrides, `NO_COLOR`, `TERM=dumb`, and unchanged JSON. Terminal width is measured
once for text reports and is capped at 100 columns for prose.
