# Local validation

Validation completed on 2026-10-06.

| Environment | Result |
| --- | --- |
| Windows, Python 3.11.4 | 60 tests passed; 2 Linux-only integration tests skipped. |
| Ubuntu WSL, Python 3.10.12, x86-64 GNU GCC/binutils | All 62 tests passed, including assembly and runtime integration. |

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
