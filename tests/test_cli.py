import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from assembly_convention_checker.cli import main


class CliTests(unittest.TestCase):
    def invoke(self, source, arguments=()):
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO(source)), contextlib.redirect_stdout(output):
            status = main(["-", *arguments])
        return status, output.getvalue()

    def test_success(self):
        status, output = self.invoke(".globl f\nf: ret\n")
        self.assertEqual(0, status)
        self.assertIn("No issues found in the supported checks", output)
        self.assertIn("complete coverage", output)

    def test_error(self):
        status, output = self.invoke(".globl f\nf: std; ret\n")
        self.assertEqual(1, status)
        for text in ("ABI_DIRECTION_FLAG", "suggestion:", "related:"):
            self.assertIn(text, output)

    def test_gap_and_warning_policies(self):
        for source in (".globl f\nf: syscall\n", ".globl f\nf: testq %rdi,%rdi; je 1f; std\n1: ret\n"):
            self.assertEqual(0, self.invoke(source)[0])
            status, output = self.invoke(source, ("--strict",))
            self.assertEqual(1, status)
            self.assertIn("Analysis is incomplete", output)
            self.assertNotIn("No issues found", output)

    def test_malformed_input(self):
        self.assertEqual(2, self.invoke(".globl f\nf: movq %rax,\n")[0])

    def test_json(self):
        status, output = self.invoke("bare: ret\n", ("--entry", "bare", "--format", "json"))
        payload = json.loads(output)
        self.assertEqual(0, status)
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual([], payload["reports"][0]["diagnostics"])
        self.assertTrue(payload["reports"][0]["complete"])

    def test_multiple_files_and_io_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "good.s"
            path.write_text(".globl f\nf: ret\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([str(path), str(path.with_name("missing.s")), "--format", "json"])
            payload = json.loads(output.getvalue())
            self.assertEqual(2, status)
            self.assertEqual(2, len(payload["reports"]))
            self.assertEqual("INPUT_IO", payload["reports"][1]["diagnostics"][0]["rule_id"])

    def test_module_entry_point(self):
        process = subprocess.run([sys.executable, "-B", "-m", "assembly_convention_checker", "-", "--strict"],
            input=".globl f\nf: ret\n", text=True, capture_output=True)
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertIn("No issues found", process.stdout)

    def test_invalid_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            main(["-", "-"])
        self.assertEqual(2, exc.exception.code)


if __name__ == "__main__": unittest.main()
