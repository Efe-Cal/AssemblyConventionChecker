"""Public reports and the small source intermediate representation."""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True, order=True)
class SourceLocation:
    filename: str
    line: int
    column: int = 1


@dataclass(frozen=True)
class Diagnostic:
    category: str
    rule_id: str
    message: str
    location: SourceLocation
    function: str | None = None
    suggestion: str = ""
    related_locations: tuple[SourceLocation, ...] = ()


@dataclass
class FunctionCoverage:
    name: str
    aliases: list[str]
    location: SourceLocation
    boundaries_inferred: bool
    total_instructions: int
    reachable_instructions: int = 0
    analyzed_instructions: int = 0
    complete: bool = True
    incomplete_checks: list[str] = field(default_factory=list)


@dataclass
class AnalysisReport:
    filename: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    functions: list[FunctionCoverage] = field(default_factory=list)
    input_errors: bool = False
    schema_version: int = 1

    @property
    def complete(self) -> bool:
        return bool(self.functions) and all(f.complete for f in self.functions) and not any(
            d.category == "analysis_gap" for d in self.diagnostics
        ) and not self.input_errors

    def to_dict(self) -> dict:
        return {**asdict(self), "complete": self.complete}

    def finish(self) -> "AnalysisReport":
        grouped = {}
        for diagnostic in self.diagnostics:
            key = (diagnostic.category, diagnostic.rule_id, diagnostic.message,
                diagnostic.location, diagnostic.function, diagnostic.suggestion)
            grouped.setdefault(key, set()).update(diagnostic.related_locations)
        self.diagnostics = sorted((Diagnostic(*key, tuple(sorted(related))) for key, related in grouped.items()), key=lambda d: (
            d.location, d.function or "", d.rule_id, d.category, d.message
        ))
        return self


@dataclass(frozen=True)
class Register:
    name: str
    parent: str
    width: int
    shift: int = 0
    gpr: bool = True


@dataclass(frozen=True)
class Operand:
    kind: str
    text: str
    value: int | None = None
    register: Register | None = None
    base: Register | None = None
    index: Register | None = None
    scale: int = 1
    indirect: bool = False


@dataclass(frozen=True)
class Instruction:
    mnemonic: str
    operands: tuple[Operand, ...]
    location: SourceLocation
    section: int
    text: str
    unsupported: str = ""


@dataclass(frozen=True)
class Label:
    name: str
    position: int
    section: int
    location: SourceLocation


@dataclass
class Function:
    name: str
    aliases: list[str]
    start: int
    end: int
    location: SourceLocation
    inferred: bool


@dataclass
class Program:
    instructions: list[Instruction] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    functions: list[Function] = field(default_factory=list)
