"""Run the Python suite from its package, without requiring an installation."""
import subprocess
import sys
from pathlib import Path

raise SystemExit(subprocess.call(
    [sys.executable, "-B", "-m", "unittest", "discover", "-v"],
    cwd=Path(__file__).resolve().parents[1] / "packages" / "checker",
))
