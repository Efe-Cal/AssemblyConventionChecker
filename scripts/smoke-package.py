"""Verify a packaged extension outside the checkout with an uninstalled checker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

root = Path(__file__).resolve().parents[1]
archive = root / "dist" / "assembly-convention-checker-0.1.0.vsix"
with tempfile.TemporaryDirectory(prefix="acc-vsix-smoke-") as temporary:
    directory = Path(temporary)
    with zipfile.ZipFile(archive) as package:
        for member in package.namelist():
            if not (directory / member).resolve().is_relative_to(directory.resolve()):
                raise RuntimeError("Invalid package member")
        package.extractall(directory)
    extension = directory / "extension"
    for asset in ("out/host/extension.js", "out/webview/report.css", "out/webview/report.js", "python/runner.py", "python/assembly_convention_checker/analyzer.py", "media/icon.png"):
        assert (extension / asset).is_file(), asset
    assert not (extension / "node_modules").exists()
    # A clean venv ensures the checker isn't installed. The bundled launcher supplies it.
    subprocess.run([sys.executable, "-m", "venv", str(directory / "venv")], check=True)
    python = directory / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-c", "import importlib.util; assert importlib.util.find_spec('assembly_convention_checker') is None"], cwd=directory, check=True)
    check = subprocess.run([str(python), "-I", "-S", "-B", "-X", "utf8", str(extension / "python/runner.py"), "-", "--format", "json"], input=".globl f\nf: ret\n", cwd=directory, text=True, capture_output=True, check=True)
    assert json.loads(check.stdout)["reports"][0]["complete"]
    env = {**os.environ, "ACC_EXTENSION_PATH": str(extension), "ACC_TEST_WORKSPACE": str(directory / "workspace"), "ACC_TEST_PYTHON": str(python)}
    # The test host uses automatic discovery, so expose only this clean venv's Python first.
    env["PATH"] = str(python.parent) + os.pathsep + env["PATH"]
    settings = directory / "workspace" / ".vscode"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(json.dumps({"assemblyConventionChecker.pythonPath": str(python)}))
    subprocess.run(["node", str(root / "packages/vscode/test/integration.cjs")], cwd=directory, env=env, check=True)
    destination = root / "artifacts" / "package-smoke-result.json"
    destination.write_text(json.dumps({"passed": True, "package": archive.name, "outside_checkout": True, "checker_installed": False, "isolated_interpreter": str(python), "extension_host_checks": True}, indent=2))
    print("Packaged extension smoke test passed outside the checkout.")
