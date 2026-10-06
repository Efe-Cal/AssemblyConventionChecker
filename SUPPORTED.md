# Supported source

## Syntax and discovery

- `%` register prefixes and `$` immediate prefixes; `*` for indirect calls/jumps.
- Integer GPR aliases, including high-byte registers; 8/16/32/64-bit operand sizes.
- Memory addressing `displacement(base,index,scale)`, with optional components
  and scale 1, 2, 4, or 8. RIP-relative addresses parse but are not stack locations.
- Named labels, `.L` labels, and repeated numeric labels with `1f`/`1b` references.
- `#` and `/` line comments, `/* ... */` block comments, quoted directive strings,
  semicolon-separated statements, and labels preceding instructions.
- Decimal, hexadecimal, binary, and GNU-style octal integer literals. Integer
  constants support unary `+`, `-`, `~` and binary `+`, `-`, `*`, `<<`, `>>`, `&`,
  `|`, `^`, with parentheses. Expressions have 128-bit and nesting limits.
- `.equ`, `.equiv`, `.set`, and `name = expression`. Definitions are evaluated in
  source order. Forward/unknown symbols remain unknown rather than being guessed.

Entries come from `.type name,@function` (also `%function`, `"function"`, or
`STT_FUNC` forms), labeled CFI regions, global labels in executable sections, or
explicit `--entry` selections. Consecutive labels at the same instruction are
aliases. Inferred entries and inferred end boundaries are recorded.

Matching `.size name,.-name` and `.cfi_endproc` close function regions. Otherwise,
the next entry, executable-section segment boundary, or EOF closes the region.
Conflicting boundaries and reachable fallthrough produce gaps.

Section handling includes `.text`, `.data`, `.bss`, `.rodata`, `.section`,
`.pushsection`, `.popsection`, and `.previous`. `.section` flags determine whether
code is executable; absent flags default `.text*` names to executable.

Allowed metadata includes `.file`, `.loc`, `.ident`, visibility/linkage directives,
`.code64`, `.att_syntax [prefix]`, CFI directives, and ordinary alignment
directives. CFI is never trusted as evidence of register restoration. Executable
alignment with an explicit fill supports only the NOP byte `0x90`.

Ordinary data directives are ignored in non-executable sections. In executable
sections, byte encodings, data, space, fill, and `.org` stop the affected path.

## Instructions

| Family | Supported instructions |
| --- | --- |
| Moves | `mov`, `movabs`, `lea`, `xchg`, `movs{b,w}{w,l,q}`, `movz{b,w}{w,l,q}`, `movslq` |
| Stack | `push`, `pop`, `leave`, `ret`, `ret $imm16` |
| Arithmetic | `add`, `sub`, `adc`, `sbb`, `inc`, `dec`, `neg`, `imul`, `mul`, `idiv`, `div` |
| Bit operations | `and`, `or`, `xor`, `not`, `shl`/`sal`, `shr`, `sar`, `rol`, `ror` |
| Comparison | `cmp`, `test`, `cmovcc`, `setcc` |
| Transfers | Direct `jmp`, `jcc`, `loop`, `loope`/`loopz`, `loopne`/`loopnz`, `jrcxz`, `jecxz`, direct/indirect `call` |
| Conversion | `cbtw`/`cbw`, `cwtl`/`cwde`, `cltq`/`cdqe`, `cwtd`/`cwd`, `cltd`/`cdq`, `cqto`/`cqo` |
| Other | `cld`, `std`, `nop`/`nopl`/`nopw`, `endbr64` |

Standard `b/w/l/q` suffixes select operand size where appropriate. Without a
suffix, register operands normally supply the size; memory-only integer
operations default to 32 bits. Push/pop default to 64 bits. Incompatible operand
counts, sizes, register names, and supported instruction forms are input errors.
The parser does not replace GNU assembler's complete encoding validation.

Register effects follow the [Intel instruction manuals](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html).
The analyzer handles implicit `%rax`/`%rdx` destinations, partial-register writes,
32-bit zero extension, and conditional writes. Abstract division results and
nonconstant bit transformations may remain unknown.

Direct jumps to known function entries or external symbols are tail candidates.
`jmp .` refers to the current instruction. Calls to internal labels without
identified function entries produce a gap for the unmarked helper body.
Indirect jumps and unmodeled instructions stop their reachable path with a gap.
Independent paths are still analyzed. Unreachable unmodeled instructions do not
make the selected function's coverage incomplete.

## Expansion and unsupported conventions

Macros, includes, assembler conditionals/repetition, and unprocessed C
preprocessor controls block the file. Supply expanded plain assembly instead.
Intel syntax or `.code16`/`.code32` modes block the file. Quoted/nonstandard label
names, jump tables, multi-segment functions, SIMD/x87 state, signals, nonlocal
transfers, exceptions, `_start`, and custom ABIs require manual review.

All normal calls are assumed to return. No special symbol-name summaries for
`exit`, `abort`, or other non-returning routines are built in.
