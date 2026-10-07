# Assembly Convention Checker

A Python ABI checker and a VS Code extension in one monorepo. Inspect GNU AT&T
assembly as you edit, then open a precise report beside the source to review
findings, correction guidance, and function coverage.

## Install the extension

Build the VSIX with Node.js 22+:

```console
npm ci
npm run package
```

In VS Code, run **Extensions: Install from VSIX…** and select
`dist/assembly-convention-checker-0.1.0.vsix`. Python 3.11+ must be available on
the extension host. The checker is bundled; no pip installation is needed.
For WSL or SSH, install the extension and Python in that remote environment.

Open a `.s` or `.S` file, then choose **Assembly Convention Checker: Open Report**.
Changes are checked after a 400 ms pause. Assembly is never assembled or executed.

See the [extension guide](packages/vscode/README.md) for commands and settings.

## Repository

| Package | Purpose |
| --- | --- |
| [`packages/checker`](packages/checker/README.md) | Dependency-free Python engine, CLI, tests, and assembly examples. |
| [`packages/vscode`](packages/vscode/README.md) | TypeScript extension, report view, runtime bridge, and VSIX packaging. |

The extension consumes the existing schema-version-1 CLI JSON. There is one
analysis engine, and its installed Python API and `abi-check` command are preserved.

## Develop and verify

```console
npm ci
npm run build
npm test
npm run test:integration
npm run test:visual
npm run package
npm run test:package
```

Press **F5** from the repository root to launch the extension with assembly examples.
`npm run watch` watches TypeScript; rerun the build after editing CSS or Python.
Visual tests require Chromium: `npx playwright install chromium`.
Extension-host tests download VS Code 1.100.3, or use a local executable through
`ACC_VSCODE_EXECUTABLE`. Test profiles and screenshots are saved in `artifacts/`.
`npm run test:package` extracts the VSIX into a temporary directory and verifies it
in VS Code with a clean Python environment that has no checker installed.

Run the standalone checker from its package:

```console
cd packages/checker
python -m assembly_convention_checker examples/broken.s --format json
python -m unittest discover -v
```

Or install from the root with `python -m pip install -e packages/checker` and use
`abi-check` anywhere. Linux integration tests execute only checked-in trusted fixtures.

This release checks ordinary Linux/ELF System V AMD64 functions. `.S` files must
already be expanded. A clean report means **“No issues found in the supported
checks,”** not whole-ABI certification. See [supported source](packages/checker/SUPPORTED.md)
and the [analysis design](packages/checker/DESIGN.md).
