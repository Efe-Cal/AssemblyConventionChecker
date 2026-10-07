"""Command-line I/O and report rendering."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import textwrap

from . import analyze


RULE_TITLES = {
    "ABI_STACK_ALIGNMENT": "Stack misaligned at transfer",
    "ABI_STACK_RESTORE": "Stack pointer not restored",
    "ABI_RETURN_ADDRESS": "Return address not preserved",
    "ABI_CALLEE_SAVED": "Callee-saved register not preserved",
    "ABI_DIRECTION_FLAG": "Direction flag must be clear",
    "ABI_RED_ZONE_BOUNDS": "Access outside protected stack storage",
    "ABI_RED_ZONE_LIVE": "Stack data may be clobbered by a call",
    "ABI_RET_CLEANUP": "Unexpected stack cleanup on return",
    "INPUT_IO": "Cannot read source file",
}


def _safe(text):
    """Escape terminal controls, including controls embedded in filenames."""
    return "".join(f"\\x{ord(c):02x}" if ord(c) < 32 or 127 <= ord(c) < 160 else c
                   for c in str(text))


def _style(text, code, color):
    return f"\033[{code}m{text}\033[0m" if color else text


def _prose(text, width, prefix=""):
    text = _safe(" ".join(str(text).split()))
    return textwrap.wrap(text, width=width, initial_indent=prefix,
                         subsequent_indent=" " * len(prefix), break_on_hyphens=False) or [prefix]


def _location(loc):
    return f"{_safe(loc.filename)}:{loc.line}:{loc.column}"


def _excerpt(filename, lines, primary, related, color, severity):
    marks = {}
    selected = set()
    notes = []
    for loc in dict.fromkeys((primary, *related)):
        if loc.filename != filename or not 1 <= loc.line <= len(lines):
            label = "source unavailable" if loc == primary else "related operation"
            notes.append(f"   = {label}: {_location(loc)}")
            continue
        selected.add(loc.line)
        if loc == primary:
            selected.update(range(max(1, loc.line - 1), min(len(lines), loc.line + 1) + 1))
        marks.setdefault(loc.line, []).append(loc)
    output = []
    gutter = len(str(max(selected))) if selected else 1
    bar = " " * (gutter + 1) + " |"
    if selected:
        output.append(bar)
    previous = None
    for number in sorted(selected):
        if previous is not None and number > previous + 1:
            output.append(" " * (gutter + 1) + " ...")
        raw = lines[number - 1]
        output.append(f" {number:>{gutter}} | {_safe(raw.expandtabs(4))}")
        for loc in marks.get(number, ()):
            start = min(max(loc.column - 1, 0), len(raw))
            token = re.match(r"[^\s,;()]+", raw[start:])
            end = start + len(token.group()) if token else start + 1
            before = _safe(raw[:start].expandtabs(4))
            expanded = _safe(raw[:end].expandtabs(4))
            underline = ("^" if loc == primary else "-") * max(1, len(expanded) - len(before))
            if loc == primary:
                underline = _style(underline, severity, color)
            else:
                underline += " related operation"
            output.append(bar + " " + " " * len(before) + underline)
        previous = number
    if selected:
        output.append(bar)
    return output + notes


def render_text(report, source, *, width=88, color=False, included_sources=None):
    """Render compiler-style diagnostics without changing the report data."""
    width = max(24, min(width, 100))
    lines = source.split("\n") if source else []
    lines = [line.removesuffix("\r") for line in lines]
    diagnostics = sorted(report.diagnostics, key=lambda d: (
        d.location, d.function or "", d.rule_id, d.category, d.message))
    counts = [sum(d.category == category for d in diagnostics)
              for category in ("error", "warning", "analysis_gap")]
    output = [_style(_safe(report.filename), "1", color)]
    labels = ("error", "warning", "analysis gap")
    output.extend(_prose(" | ".join(f"{count} {label}{'' if count == 1 else 's'}"
                                  for count, label in zip(counts, labels)), width))
    warnings = {(d.location, d.function) for d in diagnostics if d.category == "warning"}
    attached = {}
    for d in diagnostics:
        if d.rule_id == "ANALYSIS_UNKNOWN" and (d.location, d.function) in warnings:
            attached.setdefault((d.location, d.function), []).append(d)
    grouped_notes = [d for notes in attached.values() for d in notes]
    for d in diagnostics:
        key = (d.location, d.function)
        if d in grouped_notes:
            continue
        extra = attached.pop(key, []) if d.category == "warning" else []
        severity = {"error": "1;31", "warning": "1;33", "analysis_gap": "1;36"}.get(d.category, "1")
        label = "analysis gap" if d.category == "analysis_gap" else d.category
        title = RULE_TITLES.get(d.rule_id, d.message)
        output.append("")
        output.extend(_style(line, severity, color)
                      for line in _prose(f"{label}[{d.rule_id}]: {title}", width))
        output.append(f"  --> {_location(d.location)}")
        related = (*d.related_locations, *(loc for note in extra for loc in note.related_locations))
        excerpt_source = (included_sources or {}).get(d.location.filename)
        output.extend(_excerpt(d.location.filename if excerpt_source is not None else report.filename,
                               excerpt_source.splitlines() if excerpt_source is not None else lines,
                               d.location, related, color, severity))
        if d.function:
            output.extend(_prose(d.function, width, "   = function: "))
        output.extend(_prose(d.message, width, "   = note: "))
        if d.suggestion:
            output.extend(_prose(d.suggestion, width, "   = help: "))
        for note in extra:
            output.extend(_prose(f"Analysis note [{note.rule_id}]: {note.message}", width, "   = note: "))
            if note.suggestion:
                output.extend(_prose(note.suggestion, width, "   = help: "))
    output.append("")
    if not diagnostics and report.complete:
        output.extend(_prose("No issues found in the supported checks.", width))
    count = len(report.functions)
    analyzed = sum(f.analyzed_instructions for f in report.functions)
    reachable = sum(f.reachable_instructions for f in report.functions)
    status = "complete" if report.complete else "incomplete"
    summary = (f"Analysis {status}: {count} {'function' if count == 1 else 'functions'}, "
               f"{analyzed}/{reachable} reachable instructions analyzed.")
    output.extend(_prose(summary, width))
    for function in report.functions:
        if not function.complete:
            reasons = ", ".join(function.incomplete_checks) or "See reported analysis gaps"
            output.extend(_prose(f"{function.name}: {reasons}", width, "  Incomplete: "))
    inferred = [f.name for f in report.functions if f.boundaries_inferred]
    if inferred:
        output.extend(_prose(", ".join(inferred), width, "  Inferred boundaries: "))
    return "\n".join(output)


def _use_color(mode):
    if mode != "auto":
        return mode == "always"
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check ordinary System V AMD64 functions in plain GNU AT&T source.",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, width=88))
    parser.add_argument("files", nargs="+", help="source files; '-' reads standard input")
    parser.add_argument("--entry", action="append", default=[], metavar="NAME", help="select or identify an entry label (repeatable)")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                        help="terminal styling (default: auto; respects NO_COLOR and TERM=dumb)")
    parser.add_argument("--strict", action="store_true", help="also fail on possible violations and analysis gaps")
    parser.add_argument("--stdin-filename", help="path for standard input, also used to resolve relative .include files")
    parser.add_argument("--buffer-json", action="store_true", help="read {source, buffers} JSON from stdin and return included sources")
    args = parser.parse_args(argv)
    if args.files.count("-") > 1:
        parser.error("standard input may only be specified once")
    if args.buffer_json and args.files != ["-"]:
        parser.error("--buffer-json requires exactly one standard-input source")
    buffers = {}
    buffered_source = None
    if args.buffer_json:
        try:
            payload = json.load(sys.stdin)
            buffered_source, buffers = payload["source"], payload.get("buffers", {})
            if not isinstance(buffered_source, str) or not isinstance(buffers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in buffers.items()):
                raise ValueError("source and buffer values must be strings")
            buffers = {str(Path(k).resolve()): v for k, v in buffers.items()}
        except (ValueError, KeyError, TypeError) as exc:
            parser.error(f"Invalid buffer JSON: {exc}")
    reports, sources, included_sources = [], [], {}
    def load_include(name, parent):
        path = (Path(parent).parent / name).resolve()
        text = buffers[str(path)] if str(path) in buffers else path.read_text(encoding="utf-8-sig")
        included_sources[str(path)] = text
        return str(path), text

    for filename in args.files:
        try:
            source = (buffered_source if buffered_source is not None else sys.stdin.read()) if filename == "-" else Path(filename).read_text(encoding="utf-8-sig")
            source_name = (args.stdin_filename or "<stdin>") if filename == "-" else filename
            report = analyze(source, filename=source_name, entries=args.entry,
                             include_loader=load_include if source_name != "<stdin>" else None)
        except (OSError, UnicodeError) as exc:
            from .model import AnalysisReport, Diagnostic, SourceLocation
            source = ""
            report = AnalysisReport(filename, input_errors=True)
            report.diagnostics.append(Diagnostic("error", "INPUT_IO", str(exc), SourceLocation(filename, 1)))
        reports.append(report)
        sources.append(source)
    if args.format == "json":
        payload = {"schema_version": 1, "reports": [r.to_dict() for r in reports]}
        if args.buffer_json:
            payload["sources"] = included_sources
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        width = shutil.get_terminal_size(fallback=(88, 24)).columns if sys.stdout.isatty() else 88
        color = _use_color(args.color)
        print("\n\n".join(render_text(r, s, width=width, color=color, included_sources=included_sources) for r, s in zip(reports, sources)))
    if any(r.input_errors for r in reports):
        return 2
    if any(d.category == "error" or args.strict for r in reports for d in r.diagnostics):
        return 1
    return 0
