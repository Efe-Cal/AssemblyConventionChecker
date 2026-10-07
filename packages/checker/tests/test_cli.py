import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from assembly_convention_checker.cli import main, render_text
from assembly_convention_checker import analyze
from assembly_convention_checker.model import AnalysisReport, Diagnostic, SourceLocation


class CliTests(unittest.TestCase):
    def invoke(self, source, arguments=(), *, tty=False):
        output = io.StringIO()
        output.isatty = lambda: tty
        with patch("sys.stdin", io.StringIO(source)), contextlib.redirect_stdout(output):
            status = main(["-", *arguments])
        return status, output.getvalue()

    def test_success(self):
        status, output = self.invoke(".globl f\nf: ret\n")
        self.assertEqual(0, status)
        self.assertIn("No issues found in the supported checks", output)
        self.assertIn("Analysis complete:", output)

    def test_error(self):
        status, output = self.invoke(".globl f\nf: std; ret\n")
        self.assertEqual(1, status)
        for text in ("ABI_DIRECTION_FLAG", "help:", "related operation"):
            self.assertIn(text, output)

    def test_gap_and_warning_policies(self):
        for source in (".globl f\nf: syscall\n", ".globl f\nf: testq %rdi,%rdi; je 1f; std\n1: ret\n"):
            self.assertEqual(0, self.invoke(source)[0])
            status, output = self.invoke(source, ("--strict",))
            self.assertEqual(1, status)
            self.assertIn("Analysis incomplete", output)
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

    def test_includes_in_files_and_unsaved_buffers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "main.s"
            child = Path(temporary) / "helper.s"
            source = '.include "helper.s"\n.globl f\nf: subq $8,%rsp; call helper; addq $8,%rsp; ret\n'
            root.write_text(source)
            child.write_text("helper: std; ret\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([str(root)])
            self.assertEqual(1, status)
            self.assertIn("helper: std; ret", output.getvalue())
            self.assertNotIn("source unavailable", output.getvalue())
            status, output = self.invoke(json.dumps({"source": source, "buffers": {str(child): "helper: ret\n"}}),
                                         ("--stdin-filename", str(root), "--buffer-json", "--format", "json"))
            self.assertEqual(0, status)
            payload = json.loads(output)
            self.assertEqual("helper: ret\n", payload["sources"][str(child.resolve())])
            self.assertEqual(["helper", "f"], [f["name"] for f in payload["reports"][0]["functions"]])

    def test_invalid_buffer_json(self):
        for source in ('[]', '{"source":3}', '{"source":"", "buffers":null}'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                self.invoke(source, ("--buffer-json",))
            self.assertEqual(2, exc.exception.code)

    def test_invalid_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            main(["-", "-"])
        self.assertEqual(2, exc.exception.code)

    def test_uncertainty_note_shares_warning_excerpt(self):
        source = ".globl f\nf: testq %rdi,%rdi; je 1f; std\n1: ret\n"
        report = analyze(source, filename="branch.s")
        before = report.to_dict()
        output = render_text(report, source)
        self.assertEqual(1, output.count("  --> branch.s:3:4"))
        self.assertEqual(1, output.count("[ANALYSIS_UNKNOWN]"))
        self.assertIn("1 warning | 1 analysis gap", output)
        self.assertIn("Incomplete: f:", output)
        self.assertEqual(before, report.to_dict())

    def test_ordered_multiple_diagnostics_and_related_ranges(self):
        source = "\n".join(f"instruction{n}" for n in range(1, 13))
        loc = lambda line, column=1: SourceLocation("example.s", line, column)
        report = AnalysisReport("example.s", diagnostics=[
            Diagnostic("error", "SECOND", "Second problem", loc(10)),
            Diagnostic("error", "FIRST", "First problem", loc(3), related_locations=(
                loc(2), loc(3), loc(12), SourceLocation("other.s", 7, 2)))])
        output = render_text(report, source)
        self.assertIn("2 errors | 0 warnings | 0 analysis gaps", output)
        self.assertLess(output.index("error[FIRST]"), output.index("error[SECOND]"))
        first = output.split("error[SECOND]")[0]
        self.assertEqual(1, first.count(" 3 | instruction3"))
        self.assertIn(" ...", first)
        self.assertIn("12 | instruction12", first)
        self.assertIn("related operation: other.s:7:2", first)

    def test_tabs_controls_and_invalid_positions(self):
        source = "\tcall helper\x1b[31m"
        report = AnalysisReport("test.s", diagnostics=[
            Diagnostic("error", "TEST", "Unsafe \x1b text", SourceLocation("test.s", 1, 2)),
            Diagnostic("error", "INVALID", "Invalid column", SourceLocation("test.s", 1, 100000)),
            Diagnostic("error", "MISSING", "Invalid line", SourceLocation("test.s", 900)),
        ])
        output = render_text(report, source)
        self.assertIn(" 1 |     call helper\\x1b[31m", output)
        self.assertIn("   |     ^^^^", output)
        self.assertNotIn("\x1b", output)
        self.assertIn("source unavailable: test.s:900:1", output)
        self.assertLess(len(output), 2000)
        self.assertIn("source unavailable", render_text(report, ""))

    def test_prose_wrapping_and_long_source(self):
        source = "call " + "x" * 130
        report = AnalysisReport("test.s", diagnostics=[Diagnostic("error", "ABI_STACK_ALIGNMENT",
            "A detailed explanation " * 12, SourceLocation("test.s", 1), "function_name",
            "A helpful correction " * 12)])
        for width in (40, 80, 120):
            with self.subTest(width=width):
                output = render_text(report, source, width=width)
                self.assertIn(source, output)
                for line in output.splitlines():
                    if source not in line:
                        self.assertLessEqual(len(line), min(width, 100))
                self.assertIn("           ", output)

    def test_color_overrides_and_automatic_detection(self):
        source = ".globl f\nf: std; ret\n"
        with patch.dict("os.environ", {}, clear=True):
            self.assertNotIn("\x1b[", self.invoke(source)[1])
            self.assertIn("\x1b[1;31m", self.invoke(source, ("--color", "always"))[1])
            for env in ({}, {"NO_COLOR": ""}, {"TERM": "dumb"}):
                with patch.dict("os.environ", env, clear=True):
                    self.assertEqual(not env, "\x1b[" in self.invoke(source, tty=True)[1])
                    self.assertIn("\x1b[", self.invoke(source, ("--color", "always"), tty=True)[1])
                    self.assertNotIn("\x1b[", self.invoke(source, ("--color", "never"), tty=True)[1])

    def test_json_ignores_color(self):
        source = ".globl f\nf: std; ret\n"
        plain = self.invoke(source, ("--format", "json", "--color", "never"))[1]
        styled = self.invoke(source, ("--format", "json", "--color", "always"))[1]
        self.assertEqual(plain, styled)
        self.assertNotIn("\x1b", styled)

    def test_terminal_width_measured_once_and_redirect_fallback(self):
        source = ".globl f\nf: std; ret\n"
        with patch("assembly_convention_checker.cli.shutil.get_terminal_size") as size:
            size.return_value.columns = 40
            output = self.invoke(source, ("--color", "never"), tty=True)[1]
            size.assert_called_once_with(fallback=(88, 24))
            self.assertTrue(all(len(line) <= 40 for line in output.splitlines()))
            size.reset_mock()
            self.invoke(source)
            size.assert_not_called()

    def test_multiple_text_files_and_io_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            good = Path(temporary) / "good.s"
            missing = good.with_name("missing.s")
            good.write_text(".globl f\nf: ret\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([str(good), str(missing)])
            text = output.getvalue()
            self.assertEqual(2, status)
            self.assertIn(str(good) + "\n0 errors", text)
            self.assertIn(str(missing) + "\n1 error", text)
            self.assertIn("error[INPUT_IO]: Cannot read source file", text)
            self.assertIn("source unavailable", text)
            self.assertIn("Analysis incomplete: 0 functions", text)


if __name__ == "__main__": unittest.main()
