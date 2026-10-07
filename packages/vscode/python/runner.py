"""Launch only the bundled checker; never import modules from the workspace."""
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise SystemExit("Assembly Convention Checker requires Python 3.11 or newer.")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from assembly_convention_checker.cli import main

raise SystemExit(main())
