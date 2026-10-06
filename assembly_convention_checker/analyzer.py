"""Conservative, finite-height dataflow for the supported calling convention."""

from collections import deque
from dataclasses import dataclass, field, replace
import re

from .model import AnalysisReport, Diagnostic, FunctionCoverage, Operand
from .parser import REGISTERS, parse


PRESERVED = ("rbx", "rbp", "r12", "r13", "r14", "r15")
ARGUMENTS = ("rdi", "rsi", "rdx", "rcx", "r8", "r9")
PARENTS = ("rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp", "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15")
FLAGS = ("zf", "sf", "cf", "of", "pf")
CONDITIONS = {
    "e", "z", "ne", "nz", "a", "nbe", "ae", "nb", "nc", "b", "c", "nae",
    "be", "na", "g", "nle", "ge", "nl", "l", "nge", "le", "ng", "s", "ns",
    "o", "no", "p", "pe", "np", "po",
}
BINARY = {"add", "sub", "and", "or", "xor", "adc", "sbb"}
SHIFTS = {"shl", "sal", "shr", "sar", "rol", "ror"}
ORDINARY = BINARY | SHIFTS | {
    "mov", "movabs", "lea", "push", "pop", "inc", "dec", "neg", "not", "cmp",
    "test", "imul", "mul", "idiv", "div", "xchg",
}
FIXED = {
    "call": "call", "callq": "call", "ret": "ret", "retq": "ret", "leave": "leave",
    "leaveq": "leave", "cld": "cld", "std": "std", "nop": "nop", "nopl": "nop",
    "nopw": "nop", "endbr64": "endbr64", "jmp": "jmp", "jmpq": "jmp",
    "loop": "loop", "loope": "loope", "loopz": "loope", "loopne": "loopne",
    "loopnz": "loopne", "jrcxz": "jrcxz", "jecxz": "jecxz", "jcxz": "jcxz",
    "cbtw": "cbw", "cbw": "cbw", "cwtl": "cwde", "cwde": "cwde",
    "cltq": "cdqe", "cdqe": "cdqe", "cwtd": "cwd", "cwd": "cwd",
    "cltd": "cdq", "cdq": "cdq", "cqto": "cqo", "cqo": "cqo",
}


class Unsupported(ValueError):
    pass


class BadInstruction(ValueError):
    pass


@dataclass(frozen=True)
class Value:
    kind: str
    data: int | str | tuple | None = None
    offset: int = 0
    origins: tuple[int, ...] = ()

    @property
    def identity(self):
        return self.kind, self.data, self.offset


UNKNOWN = Value("unknown")


def byte_view(value: Value, size=8) -> tuple[Value, ...]:
    """Keep byte provenance so partial saves/restores preserve untouched bits."""
    if value.kind == "bytes":
        return value.data[:size]
    if value.kind == "constant":
        return tuple(Value("constant", (value.data >> (i * 8)) & 255, origins=value.origins) for i in range(size))
    if value.kind == "unknown":
        return tuple(value for _ in range(size))
    return tuple(Value("byte", (*value.identity, i), origins=value.origins) for i in range(size))


def from_bytes(values: tuple[Value, ...]) -> Value:
    origins = tuple(sorted({p for value in values for p in value.origins}))
    if all(v.kind == "constant" for v in values):
        return Value("constant", sum(v.data << (8 * i) for i, v in enumerate(values)), origins=origins)
    if all(v.kind == "unknown" for v in values):
        return Value("unknown", origins=origins)
    if len(values) == 8 and all(v.kind == "byte" for v in values):
        identity = values[0].data[:3]
        if all(v.data[:3] == identity and v.data[3] == i for i, v in enumerate(values)):
            return Value(*identity, origins=origins)
    return Value("bytes", values, origins=origins)


def has_unknown(value: Value) -> bool:
    return value.kind in ("unknown", "invalidated") or (value.kind == "bytes" and any(has_unknown(b) for b in value.data))


def unknown(*values: Value, origin: int | None = None) -> Value:
    origins = {p for value in values for p in value.origins}
    if origin is not None:
        origins.add(origin)
    return Value("unknown", origins=tuple(sorted(origins)))


def join_value(a: Value, b: Value) -> Value:
    origins = tuple(sorted(set(a.origins) | set(b.origins)))
    if a.identity == b.identity:
        return replace(a, origins=origins)
    return Value("unknown", origins=origins)


@dataclass(frozen=True)
class Cell:
    size: int
    value: Value


@dataclass
class State:
    registers: dict[str, Value]
    memory: dict[int, Cell]
    rsp_mod: int | None = 8
    df: int | None = 0
    df_origins: tuple[int, ...] = ()
    flags: dict[str, bool | None] = field(default_factory=lambda: dict.fromkeys(FLAGS))
    escaped: bool = False
    conditioned: bool = False

    @classmethod
    def initial(cls):
        registers = {r: Value("entry", r) for r in PARENTS}
        registers["rsp"] = Value("stack", 0)
        return cls(registers, {0: Cell(8, Value("return_address"))})

    def copy(self):
        return State(self.registers.copy(), self.memory.copy(), self.rsp_mod,
            self.df, self.df_origins, self.flags.copy(), self.escaped, self.conditioned)

    def join(self, other):
        memory = {}
        for address in self.memory.keys() & other.memory.keys():
            left, right = self.memory[address], other.memory[address]
            if left.size == right.size:
                memory[address] = Cell(left.size, join_value(left.value, right.value))
        return State(
            {r: join_value(self.registers[r], other.registers[r]) for r in PARENTS},
            memory,
            self.rsp_mod if self.rsp_mod == other.rsp_mod else None,
            self.df if self.df == other.df else None,
            tuple(sorted(set(self.df_origins) | set(other.df_origins))),
            {f: self.flags[f] if self.flags[f] == other.flags[f] else None for f in FLAGS},
            self.escaped or other.escaped,
            self.conditioned or other.conditioned,
        )


def decode(instruction):
    mnemonic = instruction.mnemonic
    if mnemonic in FIXED:
        return FIXED[mnemonic], None
    if mnemonic.startswith("j") and mnemonic[1:] in CONDITIONS:
        return "jcc", mnemonic[1:]
    for family in ("cmov", "set"):
        suffix = mnemonic[len(family):] if mnemonic.startswith(family) else ""
        if suffix in CONDITIONS:
            return family, suffix
        if suffix[:-1] in CONDITIONS and suffix[-1:] in "bwlq":
            return family, suffix
    if re.fullmatch(r"mov[sz][bw][wlq]", mnemonic) or mnemonic == "movslq":
        return "extend", mnemonic
    if mnemonic in ORDINARY:
        return mnemonic, None
    if mnemonic[-1:] in "bwlq" and mnemonic[:-1] in ORDINARY:
        return mnemonic[:-1], {"b": 8, "w": 16, "l": 32, "q": 64}[mnemonic[-1]]
    raise Unsupported(f"Instruction '{mnemonic}' is outside the supported instruction set.")


def width_for(ops, suffix, default=32):
    if isinstance(suffix, int):
        return suffix
    for operand in reversed(ops):
        if operand.register:
            return operand.register.width
    return default


def signed(number, width):
    number &= (1 << width) - 1
    return number - (1 << width) if number & (1 << (width - 1)) else number


def condition(state, name):
    z, s, c, o, p = (state.flags[f] for f in FLAGS)
    # Enumerate unknown booleans (at most 32 combinations), preserving logical identities.
    def evaluate(z, s, c, o, p):
        if name in ("e", "z"): return z
        if name in ("ne", "nz"): return not z
        if name in ("a", "nbe"): return not c and not z
        if name in ("ae", "nb", "nc"): return not c
        if name in ("b", "c", "nae"): return c
        if name in ("be", "na"): return c or z
        if name in ("g", "nle"): return not z and s == o
        if name in ("ge", "nl"): return s == o
        if name in ("l", "nge"): return s != o
        if name in ("le", "ng"): return z or s != o
        if name == "s": return s
        if name == "ns": return not s
        if name == "o": return o
        if name == "no": return not o
        if name in ("p", "pe"): return p
        return not p
    results = set()
    for combination in range(32):
        values = [bool(combination & (1 << i)) if v is None else v for i, v in enumerate((z, s, c, o, p))]
        results.add(evaluate(*values))
    return next(iter(results)) if len(results) == 1 else None


class Engine:
    def __init__(self, program, function, report):
        self.program, self.function, self.report = program, function, report
        self.instructions = program.instructions
        self.coverage = FunctionCoverage(function.name, function.aliases, function.location,
            function.inferred, function.end - function.start)
        self.report.functions.append(self.coverage)
        self.emitting = False
        self.pc = function.start
        self.successful: set[int] = set()

    def diagnostic(self, category, rule_id, message, suggestion="", origins=()):
        if not self.emitting:
            return
        location = self.instructions[self.pc].location if self.pc < len(self.instructions) else self.function.location
        related = tuple(sorted({self.instructions[p].location for p in origins
            if 0 <= p < len(self.instructions) and self.instructions[p].location != location}))
        self.report.diagnostics.append(Diagnostic(category, rule_id, message, location,
            self.function.name, suggestion, related))
        if category == "analysis_gap":
            self.coverage.complete = False
            if rule_id not in self.coverage.incomplete_checks:
                self.coverage.incomplete_checks.append(rule_id)
        if rule_id.startswith("INPUT_"):
            self.report.input_errors = True
            self.coverage.complete = False

    def uncertain(self, rule, message, suggestion, origins=()):
        self.diagnostic("warning", rule, message, suggestion, origins)
        self.diagnostic("analysis_gap", "ANALYSIS_UNKNOWN",
            "Some required state is unknown at this site; the affected obligations could not be established.",
            "Inspect the related operations and simplify ambiguous control flow or stack addressing.", origins)

    def need_count(self, ops, counts):
        if len(ops) not in counts:
            expected = ", ".join(str(n) for n in counts)
            raise BadInstruction(f"Expected {expected} operand(s); found {len(ops)}.")

    def supported_registers(self, operand):
        for register in (operand.register, operand.base, operand.index):
            if register and not register.gpr and register.parent != "rip":
                raise Unsupported(f"Register %{register.name} is outside the modeled general-purpose registers.")

    def read_register(self, state, register):
        if not register.gpr:
            raise Unsupported(f"Register %{register.name} cannot be read as a general-purpose value.")
        value = state.registers[register.parent]
        if register.width == 64:
            return value
        first, size = register.shift // 8, register.width // 8
        return from_bytes(byte_view(value)[first:first + size])

    def write_register(self, state, register, value):
        if not register.gpr:
            raise Unsupported(f"Register %{register.name} cannot be written as a general-purpose value.")
        previous = state.registers[register.parent]
        origins = tuple(sorted(set(value.origins) | {self.pc}))
        if register.width == 64:
            result = replace(value, origins=origins)
        else:
            fragments = list(byte_view(previous))
            first, size = register.shift // 8, register.width // 8
            fragments[first:first + size] = byte_view(value, size)
            if register.width == 32:
                fragments[4:] = [Value("constant", 0)] * 4
            result = replace(from_bytes(tuple(fragments)), origins=origins)
        state.registers[register.parent] = result
        if register.parent == "rsp":
            if result.kind == "stack":
                state.rsp_mod = (8 + result.data) % 16
            elif result.kind == "constant":
                state.rsp_mod = result.data % 16
            elif result.kind == "bytes" and result.data[0].kind == "constant":
                state.rsp_mod = result.data[0].data % 16
            else:
                state.rsp_mod = None

    def address(self, state, operand):
        if operand.kind == "symbol":
            return None
        if operand.value is None:
            return None
        base = self.read_register(state, operand.base) if operand.base and operand.base.gpr else UNKNOWN
        index = self.read_register(state, operand.index) if operand.index and operand.index.gpr else Value("constant", 0)
        if base.kind == "stack" and index.kind == "constant":
            return base.data + index.data * operand.scale + operand.value
        if operand.base is None and index.kind == "stack" and operand.scale == 1:
            return index.data + operand.value
        return None

    def check_extent(self, state, address, size):
        sp = state.registers["rsp"]
        if sp.kind == "stack" and address < sp.data - 128:
            self.diagnostic("error", "ABI_RED_ZONE_BOUNDS",
                f"A {size}-byte stack access starts below the 128-byte red zone at the current stack pointer.",
                "Allocate enough stack space before accessing this address.")

    def memory_read(self, state, address, size, *, check_extent=True):
        if check_extent:
            self.check_extent(state, address, size)
        for start, cell in sorted(state.memory.items()):
            if start <= address and address + size <= start + cell.size:
                value = cell.value
                if value.kind == "invalidated":
                    self.diagnostic("warning", "ABI_RED_ZONE_LIVE",
                        "This read relies on stack data below a previous call's stack pointer; the call may have overwritten it.",
                        "Allocate the value in the stack frame before calling, or recreate it after the call.", value.origins)
                    return unknown(value)
                if address == start and size == cell.size:
                    return value
                offset = address - start
                return from_bytes(byte_view(value, cell.size)[offset:offset + size])
        return UNKNOWN

    def memory_write(self, state, address, size, value):
        self.check_extent(state, address, size)
        for start, cell in list(state.memory.items()):
            if start < address + size and address < start + cell.size:
                del state.memory[start]
        state.memory[address] = Cell(size, replace(value, origins=tuple(sorted(set(value.origins) | {self.pc}))))

    def read(self, state, operand, width):
        self.supported_registers(operand)
        if operand.indirect:
            raise BadInstruction("'*' is only valid on an indirect call or jump operand.")
        if operand.kind == "register":
            if operand.register.width != width:
                raise BadInstruction("Register width does not match the instruction operand size.")
            return self.read_register(state, operand.register)
        if operand.kind == "immediate":
            return Value("constant", operand.value & ((1 << width) - 1)) if operand.value is not None else unknown(origin=self.pc)
        address = self.address(state, operand)
        return self.memory_read(state, address, width // 8) if address is not None else UNKNOWN

    def write(self, state, operand, width, value):
        self.supported_registers(operand)
        if operand.indirect or operand.kind == "immediate":
            raise BadInstruction("Destination must be a register or memory operand.")
        if operand.kind == "register":
            if operand.register.width != width:
                raise BadInstruction("Register width does not match the instruction operand size.")
            self.write_register(state, operand.register, value)
            return
        address = self.address(state, operand)
        if address is None:
            # Unknown stores can alias a saved register or the return address.
            state.memory = {a: Cell(c.size, unknown(c.value, origin=self.pc)) for a, c in state.memory.items()}
            if value.kind == "stack":
                state.escaped = True
        else:
            self.memory_write(state, address, width // 8, value)

    def clear_flags(self, state):
        state.flags = dict.fromkeys(FLAGS)

    def set_flags(self, state, operation, left, right, result, width):
        self.clear_flags(state)
        if operation in ("and", "or", "xor", "test"):
            state.flags.update(cf=False, of=False)
        if result.kind != "constant":
            return
        number, mask = result.data & ((1 << width) - 1), (1 << width) - 1
        state.flags.update(zf=number == 0, sf=bool(number & (1 << (width - 1))),
            pf=(number & 255).bit_count() % 2 == 0)
        if left.kind == right.kind == "constant":
            a, b = left.data & mask, right.data & mask
            sign = 1 << (width - 1)
            if operation == "add":
                state.flags.update(cf=a + b > mask, of=bool((~(a ^ b) & (a ^ number)) & sign))
            elif operation in ("sub", "cmp"):
                state.flags.update(cf=a < b, of=bool(((a ^ b) & (a ^ number)) & sign))

    def arithmetic(self, operation, left, right, width):
        mask = (1 << width) - 1
        origins = tuple(sorted(set(left.origins) | set(right.origins) | {self.pc}))
        if left.kind in ("constant", "entry", "stack", "return_address") and left.identity == right.identity and operation in ("xor", "sub", "cmp"):
            return Value("constant", 0, origins=origins)
        if right.kind == "constant":
            b = right.data & mask
            if (operation in ("add", "sub", "or", "xor") and b == 0) or (operation == "and" and b == mask):
                return replace(left, origins=origins)
            if operation in ("and", "test") and b == 0:
                return Value("constant", 0, origins=origins)
            if width == 64 and operation in ("add", "sub") and left.kind in ("stack", "entry"):
                amount = signed(b, width) * (1 if operation == "add" else -1)
                if left.kind == "stack":
                    return Value("stack", left.data + amount, origins=origins)
                return Value("entry", left.data, (left.offset + amount) & mask, origins)
            if width == 64 and operation in ("add", "sub") and left.kind == "stack_range":
                amount = signed(b, width) * (1 if operation == "add" else -1)
                return Value("stack_range", left.data + amount, left.offset + amount, origins)
            if width == 64 and operation == "and" and left.kind == "stack":
                alignment = ((~b) & mask) + 1
                if alignment <= (1 << 63) and alignment & (alignment - 1) == 0:
                    if alignment <= 16:
                        return Value("stack", left.data - ((8 + left.data) % alignment), origins=origins)
                    return Value("stack_range", left.data - alignment + 1, left.data, origins)
        if left.kind == right.kind == "constant":
            a, b = left.data & mask, right.data & mask
            operations = {"add": lambda: a + b, "sub": lambda: a - b, "cmp": lambda: a - b,
                "and": lambda: a & b, "test": lambda: a & b, "or": lambda: a | b, "xor": lambda: a ^ b,
                "imul": lambda: a * b}
            if operation in operations:
                return Value("constant", operations[operation]() & mask, origins=origins)
        return unknown(left, right, origin=self.pc)

    def check_df(self, state):
        if state.df == 1:
            self.diagnostic("error", "ABI_DIRECTION_FLAG", "The direction flag is set at a function boundary.",
                "Execute cld before calling, returning, or transferring to another function.", state.df_origins)
        elif state.df is None:
            self.uncertain("ABI_DIRECTION_FLAG", "The direction flag may be set at this function boundary.",
                "Ensure every incoming path executes cld after std.", state.df_origins)

    def check_alignment(self, state, tail=False):
        expected = 8 if tail else 0
        if state.rsp_mod is None:
            self.uncertain("ABI_STACK_ALIGNMENT", "Stack alignment cannot be established at this transfer.",
                "Keep a known stack alignment through every incoming path.", state.registers["rsp"].origins)
        elif state.rsp_mod != expected:
            self.diagnostic("error", "ABI_STACK_ALIGNMENT",
                f"%rsp modulo 16 is {state.rsp_mod}; this {'tail transfer' if tail else 'call'} requires {expected}.",
                "Account for the return address and all pushes when adjusting the stack.", state.registers["rsp"].origins)

    def check_exit(self, state, tail=False):
        sp = state.registers["rsp"]
        if sp.kind == "stack":
            if sp.data != 0:
                self.diagnostic("error", "ABI_STACK_RESTORE", f"The stack pointer is {sp.data:+d} bytes from its entry value at exit.",
                    "Undo stack allocation and pushes before exiting.", sp.origins)
        elif sp.kind in ("constant", "modified", "entry", "slice", "bytes") and not has_unknown(sp):
            self.diagnostic("error", "ABI_STACK_RESTORE", "The stack pointer does not preserve its entry value.",
                "Restore the full entry stack pointer before exiting.", sp.origins)
        else:
            self.uncertain("ABI_STACK_RESTORE", "The entry stack pointer may not be restored at exit.",
                "Restore it from a known saved frame pointer or undo all adjustments.", sp.origins)
        return_value = self.memory_read(state, 0, 8, check_extent=False)
        if return_value.kind != "return_address":
            if return_value.kind in ("unknown", "invalidated", "slice"):
                self.uncertain("ABI_RETURN_ADDRESS", "The original return-address slot may have been damaged.",
                    "Keep saves and local stores separate from the return-address slot.", return_value.origins)
            else:
                self.diagnostic("error", "ABI_RETURN_ADDRESS", "The original return-address slot has been overwritten.",
                    "Do not overwrite the entry return address.", return_value.origins)
        for name in PRESERVED:
            value = state.registers[name]
            if value.identity == ("entry", name, 0):
                continue
            if has_unknown(value):
                self.uncertain("ABI_CALLEE_SAVED", f"%{name} may not contain its original entry value at exit.",
                    f"Save and restore the full %{name} value on every exit path.", value.origins)
            elif state.conditioned and not (value.kind == "entry" and value.data == name and value.offset != 0):
                self.uncertain("ABI_CALLEE_SAVED", f"%{name} may not preserve its entry value on this conditional path.",
                    f"Restore %{name} on all paths; predicate relationships are outside this analysis.", value.origins)
            else:
                self.diagnostic("error", "ABI_CALLEE_SAVED", f"%{name} does not preserve its original entry value at exit.",
                    f"Save and restore the full %{name} value before exiting.", value.origins)
        self.check_df(state)
        if tail:
            self.check_alignment(state, tail=True)

    def destination(self, operand):
        if operand.kind in ("immediate",) or operand.indirect:
            raise BadInstruction("Destination must be a register or memory operand.")

    def direct_target(self, operand):
        if operand.indirect and operand.register and operand.register.width != 64:
            raise BadInstruction("Indirect jumps require a 64-bit register.")
        if operand.kind != "symbol" or operand.indirect:
            raise Unsupported("Only direct symbolic branch targets can be resolved; indirect control flow is incomplete.")
        name = operand.text.removesuffix("@PLT")
        if name == ".":
            return self.pc
        if match := re.fullmatch(r"(\d+)([fb])", name):
            labels = [l for l in self.program.labels if l.name == match[1]]
            if match[2] == "f":
                labels = [l for l in labels if l.position > self.pc]
                label = min(labels, key=lambda l: l.position) if labels else None
            else:
                labels = [l for l in labels if l.position <= self.pc]
                label = max(labels, key=lambda l: l.position) if labels else None
            if label is None:
                raise BadInstruction(f"Unresolved numeric local label: {name}")
        else:
            label = next((l for l in self.program.labels if l.name == name), None)
            if label is None:
                if name.startswith(".L"):
                    raise BadInstruction(f"Unresolved local label: {name}")
                return None  # external function, hence a direct tail candidate
        segment = self.instructions[self.function.start].section
        if self.function.start <= label.position < self.function.end and label.section == segment:
            return label.position
        if any(label.position == f.start and label.section == self.instructions[f.start].section
            for f in self.program.functions if f.start < len(self.instructions)):
            return None
        raise Unsupported("Branch target is outside this function and is not a known function entry.")

    def call(self, state, ops):
        self.need_count(ops, (1,))
        operand = ops[0]
        self.supported_registers(operand)
        if operand.kind == "immediate" or (operand.kind != "symbol" and not operand.indirect):
            raise BadInstruction("Use a direct symbol or a '*' indirect call operand.")
        if operand.indirect and operand.kind != "register":
            address = self.address(state, replace(operand, indirect=False))
            if address is not None:
                self.memory_read(state, address, 8)
        if operand.register and operand.register.width != 64:
            raise BadInstruction("Indirect calls require a 64-bit register.")
        if operand.register and not operand.register.gpr:
            raise BadInstruction("Indirect calls require a general-purpose register.")
        if operand.kind == "symbol" and not operand.indirect:
            target = self.direct_target(operand)
            if target is not None and not any(target == f.start for f in self.program.functions):
                self.diagnostic("analysis_gap", "ANALYSIS_UNMARKED_CALLEE",
                    "A call targets internal code without an identified callable entry; that body is not analyzed separately.",
                    "Mark the helper with .type/.size or a global entry, or select it with --entry.")
        self.check_alignment(state)
        self.check_df(state)
        sp = state.registers["rsp"]
        escaped = state.escaped or any(state.registers[r].kind == "stack" for r in ARGUMENTS)
        upper_sp = sp.data if sp.kind == "stack" else sp.offset if sp.kind == "stack_range" else None
        if upper_sp is not None:
            escaped |= any(c.value.kind == "stack" for a, c in state.memory.items() if upper_sp <= a < 0)
        for address, cell in list(state.memory.items()):
            if escaped:
                state.memory[address] = Cell(cell.size, unknown(cell.value, origin=self.pc))
            elif upper_sp is None:
                state.memory[address] = Cell(cell.size, unknown(cell.value, origin=self.pc))
            elif address < upper_sp:
                state.memory[address] = Cell(cell.size, Value("invalidated", origins=(self.pc,)))
        state.escaped = escaped
        for name in PARENTS:
            if name not in PRESERVED and name != "rsp":
                state.registers[name] = unknown(origin=self.pc)
        state.df, state.df_origins = 0, ()
        self.clear_flags(state)

    def execute(self, pc, incoming):
        self.pc = pc
        instruction = self.instructions[pc]
        state = incoming.copy()
        ops = instruction.operands
        if instruction.unsupported:
            raise Unsupported(instruction.unsupported)
        operation, suffix = decode(instruction)
        for op in ops:
            self.supported_registers(op)
        width = width_for(ops, suffix, 64 if operation in ("push", "pop") else 32)
        if operation in SHIFTS and suffix is None and ops:
            width = ops[-1].register.width if ops[-1].register else 32
        if width not in (8, 16, 32, 64):
            raise Unsupported("Unsupported operand width.")
        next_pc = pc + 1
        if operation in ("cld", "std", "endbr64", "leave", "ret", "nop", "cbw", "cwde", "cdqe", "cwd", "cdq", "cqo"):
            if operation == "nop":
                self.need_count(ops, (0, 1))
            elif operation == "ret":
                self.need_count(ops, (0, 1))
                if ops and (ops[0].kind != "immediate" or ops[0].value is None or not 0 <= ops[0].value <= 65535):
                    raise BadInstruction("ret requires a known unsigned 16-bit immediate when an operand is supplied.")
                if ops and ops[0].value:
                    self.diagnostic("error", "ABI_RET_CLEANUP", "ret with nonzero immediate cleanup violates the ordinary System V calling sequence.",
                        "Use a plain ret; the caller manages stack arguments.")
                self.check_exit(state)
                return state, []
            else:
                self.need_count(ops, (0,))
            if operation in ("cld", "std"):
                state.df, state.df_origins = int(operation == "std"), (pc,)
            elif operation == "leave":
                self.write_register(state, REGISTERS["rsp"], state.registers["rbp"])
                sp = state.registers["rsp"]
                value = self.memory_read(state, sp.data, 8) if sp.kind == "stack" else unknown(sp, origin=pc)
                self.write_register(state, REGISTERS["rbp"], value)
                self.adjust_sp(state, 8)
            elif operation in ("cbw", "cwde", "cdqe", "cwd", "cdq", "cqo"):
                size = {"cbw": 8, "cwde": 16, "cdqe": 32, "cwd": 16, "cdq": 32, "cqo": 64}[operation]
                src = REGISTERS[{8: "al", 16: "ax", 32: "eax", 64: "rax"}[size]]
                value = self.read_register(state, src)
                if operation in ("cwd", "cdq", "cqo"):
                    dest = REGISTERS[{16: "dx", 32: "edx", 64: "rdx"}[size]]
                    result = Value("constant", (1 << size) - 1 if signed(value.data, size) < 0 else 0, origins=value.origins) if value.kind == "constant" else unknown(value, origin=pc)
                else:
                    dest = REGISTERS[{8: "ax", 16: "eax", 32: "rax"}[size]]
                    result = Value("constant", signed(value.data, size) & ((1 << dest.width) - 1), origins=value.origins) if value.kind == "constant" else unknown(value, origin=pc)
                self.write_register(state, dest, result)
        elif operation in ("push", "pop"):
            self.need_count(ops, (1,))
            if not isinstance(suffix, int):
                width = ops[0].register.width if ops[0].register else 64
            if width not in (16, 64):
                raise BadInstruction("In 64-bit mode push/pop operands must be 16 or 64 bits.")
            if operation == "push":
                value = self.read(state, ops[0], width)
                self.adjust_sp(state, -width // 8)
                sp = state.registers["rsp"]
                if sp.kind == "stack":
                    self.memory_write(state, sp.data, width // 8, value)
                else:
                    state.memory = {a: Cell(c.size, unknown(c.value, origin=pc)) for a, c in state.memory.items()}
            else:
                self.destination(ops[0])
                sp = state.registers["rsp"]
                value = self.memory_read(state, sp.data, width // 8) if sp.kind == "stack" else unknown(sp, origin=pc)
                self.adjust_sp(state, width // 8)
                # Memory destinations using %rsp are evaluated after incrementing it.
                self.write(state, ops[0], width, value)
        elif operation == "call":
            self.call(state, ops)
        elif operation in ("jmp", "jcc", "loop", "loope", "loopne", "jrcxz", "jecxz", "jcxz"):
            self.need_count(ops, (1,))
            if operation != "jmp" and ops[0].indirect:
                raise BadInstruction("Conditional branches do not accept indirect operands.")
            if ops[0].kind == "register" and not ops[0].indirect:
                raise BadInstruction("Indirect jumps require the '*' prefix.")
            if operation == "jcxz":
                raise Unsupported("jcxz requires an unsupported 16-bit address-size control flow model.")
            if operation == "jmp":
                take = True
            elif operation == "jcc":
                take = condition(state, suffix)
            elif operation in ("jrcxz", "jecxz"):
                value = self.read_register(state, REGISTERS["rcx" if operation == "jrcxz" else "ecx"])
                take = value.data == 0 if value.kind == "constant" else None
            else:
                count = state.registers["rcx"]
                value = self.arithmetic("sub", count, Value("constant", 1), 64)
                self.write_register(state, REGISTERS["rcx"], value)
                take = value.data != 0 if value.kind == "constant" else None
                if operation in ("loope", "loopne"):
                    expected = operation == "loope"
                    z = state.flags["zf"]
                    if z is not None and z != expected:
                        take = False
                    elif take is not False and z is None:
                        take = None
            successors = []
            if take is None:
                state.conditioned = True
            if take is not False:
                target = self.direct_target(ops[0])
                if target is None:
                    self.check_exit(state, tail=True)
                else:
                    successors.append(target)
            if take is not True:
                successors.append(next_pc)
            return state, successors
        elif operation in ("mov", "movabs", "lea", "extend", "cmov", "set", "xchg"):
            self.need_count(ops, (1,) if operation == "set" else (2,))
            self.destination(ops[-1])
            if operation != "set" and all(o.kind in ("memory", "symbol") for o in ops):
                raise BadInstruction("This instruction cannot have two memory operands.")
            if operation == "lea":
                if ops[0].kind not in ("memory", "symbol") or ops[1].kind != "register" or width not in (16, 32, 64):
                    raise BadInstruction("lea requires a memory address and a word/dword/qword register destination.")
                address = self.address(state, ops[0])
                result = Value("stack", address, origins=(pc,)) if address is not None else unknown(origin=pc)
            elif operation == "extend":
                source_size = {"b": 8, "w": 16, "l": 32}[suffix[4]]
                dest_size = {"w": 16, "l": 32, "q": 64}[suffix[5]]
                if ops[0].kind == "immediate" or ops[1].kind != "register":
                    raise BadInstruction("Extension moves require a register/memory source and register destination.")
                value = self.read(state, ops[0], source_size)
                result = Value("constant", (signed(value.data, source_size) if suffix[3] == "s" else value.data) & ((1 << dest_size) - 1), origins=value.origins) if value.kind == "constant" else unknown(value, origin=pc)
                width = dest_size
            elif operation == "set":
                if ops[0].kind == "register" and ops[0].register.width != 8:
                    raise BadInstruction("setcc requires an 8-bit destination.")
                suffix = suffix[:-1] if suffix not in CONDITIONS else suffix
                truth = condition(state, suffix)
                result = Value("constant", int(truth)) if truth is not None else Value("modified", "setcc", origins=(pc,))
                width = 8
            elif operation == "cmov":
                if ops[0].kind == "immediate" or ops[1].kind != "register":
                    raise BadInstruction("cmov requires a register/memory source and register destination.")
                name = suffix if suffix in CONDITIONS else suffix[:-1]
                width = ops[1].register.width if suffix in CONDITIONS else {"w": 16, "l": 32, "q": 64}.get(suffix[-1], 8)
                if width == 8:
                    raise BadInstruction("cmov does not support byte operands.")
                value = self.read(state, ops[0], width)
                old = self.read(state, ops[1], width)
                truth = condition(state, name)
                if truth is False:
                    if width == 32:
                        self.write(state, ops[1], width, old)
                    return state, [next_pc]
                if truth is None:
                    alternate = state.copy()
                    alternate.conditioned = True
                    self.write(alternate, ops[1], width, value)
                    if width == 32:
                        self.write(state, ops[1], width, old)
                    return state.join(alternate), [next_pc]
                result = value
            else:
                if operation == "xchg" and any(o.kind == "immediate" for o in ops):
                    raise BadInstruction("xchg cannot use an immediate operand.")
                result = self.read(state, ops[0], width)
                if operation == "xchg":
                    old = self.read(state, ops[1], width)
                    # Resolve both memory addresses before either register changes.
                    address0 = self.address(state, ops[0]) if ops[0].kind != "register" else None
                    address1 = self.address(state, ops[1]) if ops[1].kind != "register" else None
                    if address0 is not None:
                        self.memory_write(state, address0, width // 8, old)
                    else:
                        self.write(state, ops[0], width, old)
                    if address1 is not None:
                        self.memory_write(state, address1, width // 8, result)
                    else:
                        self.write(state, ops[1], width, result)
                    return state, [next_pc]
            self.write(state, ops[-1], width, result)
        elif operation in BINARY | {"cmp", "test"}:
            self.need_count(ops, (2,))
            self.destination(ops[1])
            if all(o.kind in ("memory", "symbol") for o in ops):
                raise BadInstruction("Integer binary operations cannot have two memory operands.")
            right, left = self.read(state, ops[0], width), self.read(state, ops[1], width)
            actual = operation
            original_right, carry = right, None
            if operation in ("adc", "sbb"):
                carry = state.flags["cf"]
                if carry is not None and right.kind == "constant":
                    right = replace(right, data=right.data + int(carry))
                    actual = "add" if operation == "adc" else "sub"
                else:
                    actual = operation
            result = self.arithmetic(actual, left, right, width)
            same_register = ops[0].kind == "register" and ops[0] == ops[1]
            if same_register and actual in ("xor", "sub", "cmp"):
                result = Value("constant", 0, origins=(pc,))
            self.set_flags(state, actual, left, right, result, width)
            if same_register and actual in ("sub", "cmp"):
                state.flags.update(cf=False, of=False)
            if operation in ("adc", "sbb") and carry is not None:
                self.set_flags(state, actual, left, original_right, result, width)
                if left.kind == original_right.kind == "constant":
                    a, b = left.data & ((1 << width) - 1), original_right.data & ((1 << width) - 1)
                    state.flags["cf"] = a + b + int(carry) > (1 << width) - 1 if operation == "adc" else a < b + int(carry)
            old_mod = state.rsp_mod
            if operation not in ("cmp", "test"):
                self.write(state, ops[1], width, result)
                if ops[1].register and ops[1].register.parent == "rsp" and width == 64:
                    if result.kind in ("unknown", "stack_range") and right.kind == "constant":
                        if operation in ("add", "sub") and old_mod is not None:
                            state.rsp_mod = (old_mod + right.data * (1 if operation == "add" else -1)) % 16
                        elif operation == "and" and right.data & 15 == 0:
                            state.rsp_mod = 0
        elif operation in ("inc", "dec", "neg", "not"):
            self.need_count(ops, (1,))
            self.destination(ops[0])
            value = self.read(state, ops[0], width)
            old_cf = state.flags["cf"]
            if operation in ("inc", "dec"):
                right = Value("constant", 1)
                actual = "add" if operation == "inc" else "sub"
                result = self.arithmetic(actual, value, right, width)
                self.set_flags(state, actual, value, right, result, width)
                state.flags["cf"] = old_cf
            elif operation == "neg":
                result = self.arithmetic("sub", Value("constant", 0), value, width)
                self.set_flags(state, "sub", Value("constant", 0), value, result, width)
            else:
                result = self.arithmetic("xor", value, Value("constant", (1 << width) - 1), width)
            self.write(state, ops[0], width, result)
        elif operation in SHIFTS:
            self.need_count(ops, (1, 2))
            dest = ops[-1]
            self.destination(dest)
            value = self.read(state, dest, width)
            count = Value("constant", 1)
            if len(ops) == 2:
                if ops[0].kind == "register" and ops[0].register.name != "cl":
                    raise BadInstruction("Variable shift counts must use %cl.")
                if ops[0].kind not in ("register", "immediate"):
                    raise BadInstruction("Shift count must be an immediate or %cl.")
                count = self.read(state, ops[0], 8)
            if count.kind == "constant":
                n = count.data & (63 if width == 64 else 31)
                if operation in ("rol", "ror"):
                    n %= width
                if n == 0:
                    if width == 32:
                        self.write(state, dest, width, value)
                    return state, [next_pc]
                if value.kind == "constant":
                    number, mask = value.data, (1 << width) - 1
                    if operation in ("shl", "sal"): number <<= n
                    elif operation == "shr": number >>= n
                    elif operation == "sar": number = signed(number, width) >> n
                    elif operation == "rol": number = (number << n) | (number >> (width - n))
                    else: number = (number >> n) | (number << (width - n))
                    result = Value("constant", number & mask, origins=value.origins)
                else:
                    result = unknown(value, origin=pc)
            else:
                result = unknown(value, count, origin=pc)
            if operation in ("rol", "ror"):
                state.flags.update(cf=None, of=None)
            else:
                self.clear_flags(state)
            self.write(state, dest, width, result)
        elif operation in ("imul", "mul", "div", "idiv"):
            self.need_count(ops, (1, 2, 3) if operation == "imul" else (1,))
            if len(ops) == 1:
                if ops[0].kind == "immediate":
                    raise BadInstruction("One-operand multiplication/division cannot use an immediate.")
                source = self.read(state, ops[0], width)
                accumulator = REGISTERS[{8: "al", 16: "ax", 32: "eax", 64: "rax"}[width]]
                old = self.read_register(state, accumulator)
                result = unknown(source, old, origin=pc)
                if operation in ("mul", "imul") and source.kind == old.kind == "constant":
                    product = (signed(source.data, width) * signed(old.data, width)) if operation == "imul" else source.data * old.data
                    if width == 8:
                        self.write_register(state, REGISTERS["ax"], Value("constant", product & 65535))
                    else:
                        self.write_register(state, accumulator, Value("constant", product & ((1 << width) - 1)))
                        self.write_register(state, REGISTERS[{16: "dx", 32: "edx", 64: "rdx"}[width]], Value("constant", (product >> width) & ((1 << width) - 1)))
                elif width == 8:
                    self.write_register(state, REGISTERS["ax"], result)
                else:
                    self.write_register(state, accumulator, result)
                    self.write_register(state, REGISTERS[{16: "dx", 32: "edx", 64: "rdx"}[width]], result)
            else:
                if ops[-1].kind != "register" or width == 8:
                    raise BadInstruction("Two/three-operand imul requires a word/dword/qword register destination.")
                if len(ops) == 3 and ops[0].kind != "immediate":
                    raise BadInstruction("Three-operand imul requires an immediate first operand.")
                left = self.read(state, ops[-1] if len(ops) == 2 else ops[1], width)
                right = self.read(state, ops[0], width)
                self.write(state, ops[-1], width, self.arithmetic("imul", left, right, width))
            self.clear_flags(state)
        else:
            raise Unsupported("Unsupported instruction effects.")
        return state, [next_pc]

    def adjust_sp(self, state, amount):
        old_mod = state.rsp_mod
        value = self.arithmetic("add", state.registers["rsp"], Value("constant", amount), 64)
        self.write_register(state, REGISTERS["rsp"], value)
        if value.kind in ("unknown", "stack_range") and old_mod is not None:
            state.rsp_mod = (old_mod + amount) % 16

    def successors(self, pc, state):
        try:
            result, successors = self.execute(pc, state)
            self.successful.add(pc)
            return result, successors
        except BadInstruction as exc:
            self.diagnostic("error", "INPUT_INSTRUCTION", str(exc), "Correct the operand syntax or instruction form.")
        except Unsupported as exc:
            self.diagnostic("analysis_gap", "ANALYSIS_UNSUPPORTED", str(exc),
                "Rewrite this path using supported instructions or inspect its ABI obligations manually.")
        return state, []

    def run(self):
        if self.function.name == "_start" or "_start" in self.function.aliases:
            self.report.diagnostics.append(Diagnostic("analysis_gap", "ANALYSIS_ENTRY_CONVENTION",
                "Process entry does not use an ordinary function entry convention.", self.function.location,
                self.function.name, "Check ordinary callable functions separately from process startup."))
            self.coverage.complete = False
            self.coverage.incomplete_checks.append("ANALYSIS_ENTRY_CONVENTION")
            return
        if self.function.start == self.function.end:
            self.report.diagnostics.append(Diagnostic("analysis_gap", "ANALYSIS_EMPTY_FUNCTION",
                "The function has no analyzable instructions.", self.function.location, self.function.name))
            self.coverage.complete = False
            self.coverage.incomplete_checks.append("ANALYSIS_EMPTY_FUNCTION")
            return
        # Validate modeled operand forms even in dead code. Unsupported effects stay reachability-sensitive.
        malformed = False
        for pc in range(self.function.start, self.function.end):
            try:
                self.execute(pc, State.initial())
            except BadInstruction as exc:
                self.emitting = True
                self.diagnostic("error", "INPUT_INSTRUCTION", str(exc), "Correct the operand syntax or instruction form.")
                self.emitting = False
                malformed = True
            except Unsupported:
                pass
        if malformed:
            return
        states = {self.function.start: State.initial()}
        queue = deque([self.function.start])
        queued = {self.function.start}
        steps, limit = 0, max(10000, 200 * self.coverage.total_instructions)
        while queue:
            pc = queue.popleft()
            queued.remove(pc)
            result, successors = self.successors(pc, states[pc])
            for target in successors:
                if not self.function.start <= target < self.function.end:
                    continue
                merged = states[target].join(result) if target in states else result.copy()
                if target not in states or merged != states[target]:
                    states[target] = merged
                    if target not in queued:
                        queue.append(target)
                        queued.add(target)
            steps += 1
            if steps > limit:
                self.emitting = True
                self.diagnostic("analysis_gap", "ANALYSIS_LIMIT", "Analysis exceeded its work limit; results are incomplete.",
                    "Reduce the function or inspect its control flow manually.")
                return
        # Emit only from stable incoming states, avoiding errors from transient loop/branch states.
        self.emitting = True
        self.successful.clear()
        for pc in sorted(states):
            _, successors = self.successors(pc, states[pc])
            if any(target == self.function.end for target in successors):
                self.diagnostic("analysis_gap", "ANALYSIS_FALLTHROUGH",
                    "Control can fall through the identified function boundary.",
                    "End every reachable path with a return or a valid tail transfer; check the function metadata.")
        self.coverage.reachable_instructions = len(states)
        self.coverage.analyzed_instructions = len(self.successful)
        self.coverage.incomplete_checks.sort()


def analyze(source: str, *, filename: str = "<string>", entries=()) -> AnalysisReport:
    """Analyze plain AT&T source without executing it or invoking an assembler."""
    if not isinstance(source, str):
        raise TypeError("source must be a string")
    if isinstance(entries, str):
        raise TypeError("entries must be an iterable of entry names, not one string")
    entries = tuple(entries)
    report = AnalysisReport(filename)
    program = parse(source, report, entries)
    if report.input_errors:
        return report.finish()
    for function in program.functions:
        if entries and not set(entries).intersection(function.aliases):
            continue
        Engine(program, function, report).run()
    for diagnostic in report.diagnostics:
        if diagnostic.category == "analysis_gap" and diagnostic.rule_id == "ANALYSIS_BOUNDARY":
            for coverage in report.functions:
                if coverage.name == diagnostic.function:
                    coverage.complete = False
                    if diagnostic.rule_id not in coverage.incomplete_checks:
                        coverage.incomplete_checks.append(diagnostic.rule_id)
    return report.finish()
