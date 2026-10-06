"""A deliberately bounded GNU AT&T source parser; no assembler or eval."""

import ast
import operator
import re

from .model import Diagnostic, Function, Instruction, Label, Operand, Program, Register, SourceLocation


class ParseError(ValueError):
    pass


REGISTERS: dict[str, Register] = {}
for parent, names in {
    "rax": ("rax", "eax", "ax", "al", "ah"),
    "rbx": ("rbx", "ebx", "bx", "bl", "bh"),
    "rcx": ("rcx", "ecx", "cx", "cl", "ch"),
    "rdx": ("rdx", "edx", "dx", "dl", "dh"),
    "rsi": ("rsi", "esi", "si", "sil"),
    "rdi": ("rdi", "edi", "di", "dil"),
    "rbp": ("rbp", "ebp", "bp", "bpl"),
    "rsp": ("rsp", "esp", "sp", "spl"),
}.items():
    for name, width, shift in zip(names, (64, 32, 16, 8, 8), (0, 0, 0, 0, 8)):
        REGISTERS[name] = Register(name, parent, width, shift)
for number in range(8, 16):
    for suffix, width in (("", 64), ("d", 32), ("w", 16), ("b", 8)):
        name = f"r{number}{suffix}"
        REGISTERS[name] = Register(name, f"r{number}", width)
REGISTERS["rip"] = Register("rip", "rip", 64, gpr=False)


def register(text: str) -> Register:
    if not re.fullmatch(r"%[A-Za-z][A-Za-z0-9]*", text):
        raise ParseError(f"Malformed register: {text}")
    name = text[1:].lower()
    if name in REGISTERS:
        return REGISTERS[name]
    # Vector/segment and future ISA registers are recognized but not modeled.
    if re.fullmatch(r"(?:[xyz]mm\d+|mm\d+|k\d+|[cdefgs]s|r(?:1[6-9]|2\d|3[01])(?:[dwb])?)", name):
        return Register(name, name, 0, gpr=False)
    raise ParseError(f"Unknown register: {text}")


BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.LShift: operator.lshift, ast.RShift: operator.rshift,
    ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor,
}
UNOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Invert: operator.invert}


def constant(expression: str, symbols: dict[str, int | None]) -> int | None:
    """Evaluate a small integer grammar; unresolved symbols remain unknown."""
    expression = expression.strip()
    if not expression:
        return 0
    names: dict[str, str] = {}

    def rename(match):
        token = match.group()
        key = f"symbol_{len(names)}"
        names[key] = token
        return key

    expression = re.sub(r"(?<![\w.])[A-Za-z_.$][\w.$]*", rename, expression)
    # GNU accepts octal literals with a leading zero.
    expression = re.sub(r"\b0([0-7]+)\b", r"0o\1", expression)
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ParseError("Malformed constant expression") from exc

    def visit(node, depth=0):
        if depth > 32:
            raise ParseError("Constant expression is too deeply nested")
        if isinstance(node, ast.Constant) and type(node.value) is int:
            if node.value.bit_length() > 128:
                raise ParseError("Integer literal exceeds the supported 128-bit limit")
            return node.value
        if isinstance(node, ast.Name):
            return symbols.get(names.get(node.id, node.id))
        if isinstance(node, ast.UnaryOp) and type(node.op) in UNOPS:
            value = visit(node.operand, depth + 1)
            return None if value is None else UNOPS[type(node.op)](value)
        if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if left is None or right is None:
                return None
            if isinstance(node.op, (ast.LShift, ast.RShift)) and not 0 <= right <= 127:
                raise ParseError("Shift count in a constant expression must be 0..127")
            value = BINOPS[type(node.op)](left, right)
            if value.bit_length() > 128:
                raise ParseError("Constant expression exceeds the supported 128-bit limit")
            return value
        raise ParseError("Unsupported constant expression (use integers, symbols, and integer operators)")

    return visit(tree.body)


def split_operands(text: str) -> list[str]:
    result, start, depth, quoted, escaped = [], 0, 0, False, False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    raise ParseError("Unmatched closing parenthesis")
            elif char == "," and depth == 0:
                result.append(text[start:i].strip())
                start = i + 1
    if quoted or depth:
        raise ParseError("Unclosed quote or parenthesis")
    if text.strip():
        result.append(text[start:].strip())
    if any(not item for item in result):
        raise ParseError("Missing operand")
    return result


def operand(text: str, symbols: dict[str, int | None]) -> Operand:
    indirect = text.startswith("*")
    raw = text[1:].strip() if indirect else text
    if not raw:
        raise ParseError("Missing indirect operand")
    if raw.startswith("%"):
        return Operand("register", text, register=register(raw), indirect=indirect)
    if raw.startswith("$"):
        if indirect or not raw[1:].strip():
            raise ParseError("Malformed immediate operand")
        return Operand("immediate", text, value=constant(raw[1:], symbols))
    match = re.fullmatch(r"(.*?)\(([^()]*)\)", raw)
    if match:
        parts = [p.strip() for p in match[2].split(",")]
        if len(parts) > 3:
            raise ParseError("Memory operands have at most base, index, and scale")
        parts += [""] * (3 - len(parts))
        base = register(parts[0]) if parts[0] else None
        index = register(parts[1]) if parts[1] else None
        scale = constant(parts[2], symbols) if parts[2] else 1
        if scale not in (1, 2, 4, 8) or (parts[2] and index is None):
            raise ParseError("Memory scale must be 1, 2, 4, or 8 and requires an index")
        if not base and not index:
            raise ParseError("Empty addressing parentheses")
        displacement = match[1]
        value = None if "@" in displacement else constant(displacement, symbols)
        return Operand("memory", text, value, base=base, index=index, scale=scale, indirect=indirect)
    if "(" in raw or ")" in raw:
        raise ParseError("Malformed memory operand")
    if re.fullmatch(r"(?:[A-Za-z_.$][\w.$]*(?:@(?:PLT|GOTPCREL))?|\d+[fb])", raw):
        return Operand("symbol", text, indirect=indirect)
    return Operand("memory", text, value=constant(raw, symbols), indirect=indirect)


STRUCTURAL = {
    ".macro", ".endm", ".altmacro", ".purgem", ".include", ".incbin",
    ".if", ".ifdef", ".ifndef", ".ifnotdef", ".ifc", ".ifnc", ".ifeq",
    ".ifne", ".ifgt", ".ifge", ".iflt", ".ifle", ".ifb", ".ifnb",
    ".else", ".elseif", ".endif", ".rept", ".irp", ".irpc", ".endr", ".subsection",
}
METADATA = {
    ".file", ".loc", ".ident", ".hidden", ".protected", ".internal", ".weak",
    ".local", ".extern", ".p2align", ".align", ".balign", ".code64",
    ".att_syntax", ".end", ".gnu_attribute", ".note", ".symver",
}
DATA = {
    ".byte", ".word", ".short", ".long", ".int", ".quad", ".octa", ".ascii",
    ".asciz", ".string", ".zero", ".space", ".skip", ".fill", ".org",
    ".uleb128", ".sleb128", ".float", ".double", ".value",
}


def statements(source: str, filename: str):
    """Yield logical statements without losing original line/column positions."""
    block_comment, quote, escape = False, False, False
    for line_number, line in enumerate(source.splitlines(), 1):
        if not block_comment and re.match(r"^\s*#\s*(?:include|define|undef|if|ifdef|ifndef|elif|else|endif|pragma|error|warning)\b", line):
            yield line.strip(), SourceLocation(filename, line_number, len(line) - len(line.lstrip()) + 1)
            continue
        buffer, first = [], None
        i = 0
        while i < len(line):
            char = line[i]
            if block_comment:
                if line[i:i + 2] == "*/":
                    block_comment = False
                    buffer.append("  ")
                    i += 2
                else:
                    buffer.append(" ")
                    i += 1
                continue
            if not quote and line[i:i + 2] == "/*":
                block_comment = True
                buffer.append("  ")
                i += 2
                continue
            if not quote and char in "#/":
                break
            if not quote and char == ";":
                if "".join(buffer).strip():
                    yield "".join(buffer).strip(), SourceLocation(filename, line_number, first or 1)
                buffer, first = [], None
                i += 1
                continue
            if first is None and not char.isspace():
                first = i + 1
            buffer.append(char)
            if escape:
                escape = False
            elif quote and char == "\\":
                escape = True
            elif char == '"':
                quote = not quote
            i += 1
        if quote:
            raise ParseError(f"Unclosed quote on line {line_number}")
        if "".join(buffer).strip():
            yield "".join(buffer).strip(), SourceLocation(filename, line_number, first or 1)
    if block_comment:
        raise ParseError("Unclosed block comment")


def parse(source: str, report, entries: tuple[str, ...]) -> Program:
    program = Program()
    try:
        items = list(statements(source, report.filename))
    except ParseError as exc:
        report.input_errors = True
        report.diagnostics.append(Diagnostic("error", "INPUT_SYNTAX", str(exc), SourceLocation(report.filename, 1)))
        return program
    # ponytail: expansion changes source topology; delegate expansion to GNU tools before checking.
    blocked = False
    for text, location in items:
        remainder = re.sub(r"^(?:(?:[A-Za-z_.$][\w.$]*|\d+)\s*:\s*)+", "", text)
        head = remainder.split(None, 1)[0].lower() if remainder else ""
        if head in STRUCTURAL or text.startswith("#"):
            report.diagnostics.append(Diagnostic("analysis_gap", "ANALYSIS_EXPANSION",
                "This file requires assembly expansion or preprocessing; no functions were analyzed.",
                location, suggestion="Supply plain, expanded AT&T source without expansion directives."))
            blocked = True
    if blocked:
        return program

    symbols: dict[str, int | None] = {}
    global_names, typed = set(), set()
    explicit_ends: dict[str, int] = {}
    cfi_starts: dict[str, int] = {}
    cfi_ends: dict[str, int] = {}
    section, executable = 0, True
    section_state = (".text", True)
    previous = section_state
    section_stack = []
    open_cfi: Label | None = None
    last_location = SourceLocation(report.filename, 1)

    def syntax(message, location):
        report.input_errors = True
        report.diagnostics.append(Diagnostic("error", "INPUT_SYNTAX", message, location))

    def add_gap(text, location, message):
        if executable:
            program.instructions.append(Instruction("unsupported", (), location, section, text, message))
        else:
            report.diagnostics.append(Diagnostic("analysis_gap", "ANALYSIS_DIRECTIVE", message, location))

    for text, location in items:
        last_location = location
        while match := re.match(r"^([A-Za-z_.$][\w.$]*|\d+)\s*:\s*", text):
            if executable:
                label = Label(match[1], len(program.instructions), section, location)
                if not label.name.isdigit() and any(l.name == label.name for l in program.labels):
                    syntax(f"Duplicate label: {label.name}", location)
                program.labels.append(label)
            removed = match.end()
            text = text[removed:]
            location = SourceLocation(location.filename, location.line, location.column + removed)
        if not text:
            continue
        parts = text.split(None, 1)
        head, rest = parts[0].lower(), parts[1].strip() if len(parts) > 1 else ""
        try:
            if head in (".text", ".data", ".bss", ".rodata", ".section", ".pushsection", ".popsection", ".previous"):
                if head == ".pushsection":
                    section_stack.append(section_state)
                if head == ".popsection":
                    if not section_stack:
                        raise ParseError(".popsection without .pushsection")
                    new_state = section_stack.pop()
                elif head == ".previous":
                    new_state = previous
                elif head in (".section", ".pushsection"):
                    args = split_operands(rest)
                    if not args:
                        raise ParseError("Missing section name")
                    name = args[0].strip('"')
                    is_exec = "x" in args[1].strip('"') if len(args) > 1 else name.startswith(".text")
                    new_state = (name, is_exec)
                else:
                    new_state = (head, head == ".text")
                previous, section_state = section_state, new_state
                section += 1
                executable = section_state[1]
                continue
            if head in (".globl", ".global"):
                global_names.update(split_operands(rest))
                continue
            if head == ".type":
                match = re.fullmatch(r"([\w.$]+)\s*(?:,\s*|\s+)(?:[@%]?function|\"function\"|STT_FUNC)", rest, re.I)
                if match:
                    typed.add(match[1])
                elif "function" in rest.lower() or "STT_FUNC" in rest:
                    raise ParseError("Malformed function .type declaration")
                continue
            if head == ".size":
                args = split_operands(rest)
                if len(args) != 2:
                    raise ParseError(".size requires a symbol and an expression")
                if re.sub(r"\s", "", args[1]) == f".-{args[0]}":
                    explicit_ends[args[0]] = len(program.instructions)
                elif executable:
                    report.diagnostics.append(Diagnostic("analysis_gap", "ANALYSIS_BOUNDARY",
                        "Only conventional .size name,.-name boundaries are supported.", location, args[0],
                        "Use a conventional function boundary or inspect the inferred region manually."))
                continue
            if head in (".equ", ".equiv", ".set") or re.match(r"^[\w.$]+\s*=", text):
                args = split_operands(rest) if head.startswith(".") else [s.strip() for s in text.split("=", 1)]
                if len(args) != 2 or not re.fullmatch(r"[A-Za-z_.$][\w.$]*", args[0]):
                    raise ParseError("Constant definitions require a symbol and an expression")
                if head == ".equiv" and args[0] in symbols:
                    raise ParseError(".equiv cannot redefine a symbol")
                symbols[args[0]] = constant(args[1], symbols)
                continue
            if head == ".cfi_startproc":
                candidates = [l for l in program.labels if l.position == len(program.instructions) and l.section == section and not l.name.isdigit()]
                if open_cfi or not candidates:
                    raise ParseError(".cfi_startproc requires a preceding entry label and cannot nest")
                open_cfi = candidates[-1]
                cfi_starts[open_cfi.name] = open_cfi.position
                continue
            if head == ".cfi_endproc":
                if not open_cfi:
                    raise ParseError(".cfi_endproc without .cfi_startproc")
                cfi_ends[open_cfi.name] = len(program.instructions)
                open_cfi = None
                continue
            if head == ".cfi_signal_frame":
                add_gap(text, location, "Signal-frame calling conventions are outside the supported profile.")
                continue
            if head.startswith(".cfi_"):
                continue
            if head in METADATA:
                if head == ".att_syntax" and rest not in ("", "prefix"):
                    report.diagnostics.append(Diagnostic("analysis_gap", "ANALYSIS_MODE",
                        "AT&T syntax without register prefixes is unsupported; no functions were analyzed.", location,
                        suggestion="Use .att_syntax prefix and % register prefixes throughout."))
                    return Program()
                if head in (".align", ".balign", ".p2align"):
                    args = rest.split(",")
                    if len(args) > 1 and args[1].strip():
                        fill = constant(args[1], symbols)
                        if executable and fill != 0x90:
                            add_gap(text, location, "Executable alignment with a non-NOP fill byte is unsupported.")
                continue
            if head in (".intel_syntax", ".code16", ".code16gcc", ".code32"):
                report.diagnostics.append(Diagnostic("analysis_gap", "ANALYSIS_MODE",
                    "This file changes to an unsupported syntax or execution mode; no functions were analyzed.", location,
                    suggestion="Use 64-bit AT&T syntax with % register prefixes throughout."))
                program.functions = []
                return Program()
            if head in DATA or head.startswith("."):
                if executable:
                    add_gap(text, location, "Executable data encodings or this directive are unsupported.")
                elif head not in DATA and head not in (".comm", ".lcomm"):
                    add_gap(text, location, "Unknown directive may affect source interpretation.")
                continue
            if executable:
                ops = tuple(operand(arg, symbols) for arg in split_operands(rest))
                program.instructions.append(Instruction(head, ops, location, section, text))
        except ParseError as exc:
            syntax(str(exc), location)

    if open_cfi:
        syntax("Unclosed .cfi_startproc", open_cfi.location)
    if section_stack:
        syntax("Unclosed .pushsection", last_location)
    if report.input_errors:
        return program

    named = {l.name: l for l in program.labels if not l.name.isdigit()}
    selected = set(entries)
    for name in selected:
        if name not in named:
            syntax(f"Requested entry label does not exist in executable source: {name}", SourceLocation(report.filename, 1))
    if report.input_errors:
        return program
    candidates = typed | set(cfi_starts) | global_names | selected
    grouped: dict[tuple[int, int], list[Label]] = {}
    for name in candidates:
        if name in named:
            label = named[name]
            grouped.setdefault((label.position, label.section), []).append(label)
    groups = sorted(grouped.items())
    all_functions = []
    for group_number, ((start, segment), labels) in enumerate(groups):
        labels.sort(key=lambda l: (l.name not in typed, l.name not in cfi_starts, l.location, l.name))
        primary = labels[0]
        aliases = sorted(l.name for l in program.labels if l.position == start and l.section == segment and not l.name.isdigit())
        ends = {explicit_ends[n] for n in aliases if n in explicit_ends} | {cfi_ends[n] for n in aliases if n in cfi_ends}
        next_start = groups[group_number + 1][0][0] if group_number + 1 < len(groups) else len(program.instructions)
        section_end = next((i for i in range(start, len(program.instructions)) if program.instructions[i].section != segment), len(program.instructions))
        fallback = min(next_start, section_end)
        if len(ends) > 1 or any(e < start or e > fallback for e in ends):
            report.diagnostics.append(Diagnostic("analysis_gap", "ANALYSIS_BOUNDARY",
                "Function metadata has conflicting or overlapping boundaries.", primary.location, primary.name,
                "Make .size and CFI boundaries agree and keep the function in one executable section."))
        end = max(start, min(ends | {fallback}))
        has_entry_metadata = any(n in typed or n in cfi_starts for n in aliases)
        function = Function(primary.name, aliases, start, end, primary.location, not bool(ends) or not has_entry_metadata)
        all_functions.append(function)
    # Keep all discovered entries for recognizing tails even when only some are selected.
    program.functions = all_functions
    if not all_functions:
        report.diagnostics.append(Diagnostic("analysis_gap", "ANALYSIS_NO_ENTRY",
            "No callable function entry was identified; no functions were analyzed.", SourceLocation(report.filename, 1),
            suggestion="Add .type name,@function, labeled CFI metadata, .globl name, or --entry name."))
    return program
