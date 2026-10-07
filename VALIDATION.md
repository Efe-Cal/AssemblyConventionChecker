# Monorepo and extension validation

Local validation on 2026-10-06 used Windows, Python 3.11.4, Node.js 22.16.0,
VS Code 1.140.0, and Playwright Chromium. Historical checker validation remains
in [the checker package](packages/checker/VALIDATION.md).

| Check | Result |
| --- | --- |
| Python source suite | 71 tests: 69 passed, 2 Linux-only tests skipped. Includes the pre-existing instruction-suffix test. |
| Extension runtime unit tests | All 12 passed: Python execution, input errors, entry selection, JSON validation, Unicode ranges, generations, cancellation, timeout, and UTF-8 decoding. |
| VS Code extension-host integration | Passed: activation, all diagnostic severities, unsaved edits, settings, document versions, stable refresh layout, real webview script/CSP loading, report reuse, navigation, URI rejection, and import isolation. |
| Visual checks | Dark, light, and high-contrast themes at 1180 and 520 pixels; severity/function filters, keyboard navigation and focus, clean/incomplete states, setup states, long findings, escaped content, reduced motion, and overflow checks. |
| Installed CLI | The relocated Python package built and installed into a fresh virtual environment; installed `abi-check` passed the strict good-frame fixture. |
| Packaged extension | VSIX extracted outside the checkout and run in VS Code with a fresh Python environment containing no installed checker. Bundled analysis and editor integration passed. |
| Dependencies | npm audit reports zero vulnerabilities; the extension ships no Node runtime dependencies. |

Reproduce from the repository root:

```console
npm ci
npm test
npm run test:integration
npx playwright install chromium
npm run test:visual
npm run package
npm run test:package
```

Use `ACC_VSCODE_EXECUTABLE` to test an installed VS Code executable. Otherwise,
the integration runner downloads VS Code 1.100.3. Screenshots and machine-readable
results are in `artifacts/`. The CI configuration covers Windows and Ubuntu,
but the updated workflow has not yet run remotely. Marketplace publication is
not part of this delivery.
