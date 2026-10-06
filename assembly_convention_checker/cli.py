"""Command-line I/O and report rendering."""

import argparse
import json
from pathlib import Path
import sys

from . import analyze


def render_text(report, source):
    lines = source.splitlines()
    output = []
    for diagnostic in report.diagnostics:
        loc = diagnostic.location
        function = f" [{diagnostic.function}]" if diagnostic.function else ""
        output.append(f"{loc.filename}:{loc.line}:{loc.column}: {diagnostic.category} {diagnostic.rule_id}{function}: {diagnostic.message}")
        if 1 <= loc.line <= len(lines):
            line = lines[loc.line - 1]
            output.extend((f"  {line}", "  " + " " * (loc.column - 1) + "^"))
        for related in diagnostic.related_locations:
            output.append(f"  related: {related.filename}:{related.line}:{related.column}")
        if diagnostic.suggestion:
            output.append(f"  suggestion: {diagnostic.suggestion}")
    for function in report.functions:
        status = "complete" if function.complete else "incomplete"
        boundary = "; inferred boundaries" if function.boundaries_inferred else ""
        output.append(f"{report.filename}: {function.name}: {status} coverage; "
            f"{function.analyzed_instructions}/{function.reachable_instructions} reached instructions analyzed"
            f" ({function.total_instructions} total){boundary}.")
    if not report.diagnostics and report.complete:
        output.append(f"{report.filename}: No issues found in the supported checks.")
    elif not report.complete:
        output.append(f"{report.filename}: Analysis is incomplete; inspect the reported gaps.")
    return "\n".join(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check ordinary System V AMD64 functions in plain GNU AT&T source.")
    parser.add_argument("files", nargs="+", help="source files; '-' reads standard input")
    parser.add_argument("--entry", action="append", default=[], metavar="NAME", help="select or identify an entry label (repeatable)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--strict", action="store_true", help="also fail on possible violations and analysis gaps")
    args = parser.parse_args(argv)
    if args.files.count("-") > 1:
        parser.error("standard input may only be specified once")
    reports, sources = [], []
    for filename in args.files:
        try:
            source = sys.stdin.read() if filename == "-" else Path(filename).read_text(encoding="utf-8-sig")
            report = analyze(source, filename="<stdin>" if filename == "-" else filename, entries=args.entry)
        except (OSError, UnicodeError) as exc:
            from .model import AnalysisReport, Diagnostic, SourceLocation
            source = ""
            report = AnalysisReport(filename, input_errors=True)
            report.diagnostics.append(Diagnostic("error", "INPUT_IO", str(exc), SourceLocation(filename, 1)))
        reports.append(report)
        sources.append(source)
    if args.format == "json":
        print(json.dumps({"schema_version": 1, "reports": [r.to_dict() for r in reports]}, indent=2, sort_keys=True))
    else:
        print("\n\n".join(render_text(r, s) for r, s in zip(reports, sources)))
    if any(r.input_errors for r in reports):
        return 2
    if any(d.category == "error" or args.strict for r in reports for d in r.diagnostics):
        return 1
    return 0
