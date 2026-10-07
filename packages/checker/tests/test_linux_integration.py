"""Assemble and execute only the trusted, checked-in Linux test fixtures."""

from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

from assembly_convention_checker import analyze

FIXTURES = Path(__file__).parent / "fixtures"


@unittest.skipUnless(sys.platform.startswith("linux") and platform.machine() == "x86_64", "requires Linux x86-64")
class LinuxIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gcc = shutil.which("gcc")
        if not cls.gcc:
            raise unittest.SkipTest("requires ELF-targeting GNU gcc/binutils")
        target = subprocess.run([cls.gcc, "-dumpmachine"], capture_output=True, text=True, check=True).stdout
        if not target.startswith("x86_64") or "linux" not in target:
            raise unittest.SkipTest("requires an x86-64 Linux GNU toolchain")

    def test_fixture_assembler_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            for source in sorted(FIXTURES.glob("*.s")):
                with self.subTest(source=source.name):
                    process = subprocess.run([self.gcc, "-c", str(source), "-o", str(Path(directory) / (source.stem + ".o"))],
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(0, process.returncode, process.stderr)

    def test_runtime_registers_stack_df_and_red_zone(self):
        for name in ("good_frame.s", "good_leaf.s"):
            self.assertEqual([], analyze((FIXTURES / name).read_text()).diagnostics)
        self.assertIn("ABI_RED_ZONE_LIVE", [d.rule_id for d in analyze((FIXTURES / "red_zone_live.s").read_text()).diagnostics])
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "runtime-check"
            sources = [FIXTURES / name for name in (
                "runtime_main.c", "runtime_harness.s", "good_frame.s", "good_leaf.s", "red_zone_live.s")]
            build = subprocess.run([self.gcc, "-Wall", "-Wextra", "-o", str(executable), *map(str, sources)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(0, build.returncode, build.stderr)
            run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(0, run.returncode, "Runtime ABI harness failed: " + run.stderr)


if __name__ == "__main__": unittest.main()
