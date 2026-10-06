# Analysis design

The implementation uses three stages: source parsing, per-function dataflow,
and report rendering. The Python API does not perform I/O. The CLI reads UTF-8
source and renders either explanatory text or versioned JSON.

## Source intermediate representation

The parser retains original file/line/column positions for labels, instructions,
and directives. Typed operands distinguish registers, immediates, symbolic
references, and memory addressing. Numeric labels resolve by source order.

Function discovery produces nonoverlapping instruction regions and aliases.
All discovered entries remain available for recognizing tails even if the caller
selects only some functions. Metadata conflicts remain visible in the report.

## Abstract state and control flow

Each function starts with symbolic GPR entry values, `%rsp = entry_rsp`,
`%rsp mod 16 = 8`, clear DF, and a symbolic return address at entry offset zero.
The graph is represented by instruction indices and their computed successors.
Calls use ABI summaries rather than entering another function's graph.

States contain register values, tracked memory cells, stack congruence, DF,
condition flags, and whether stack addresses have escaped or unresolved
conditions have been encountered. Values distinguish constants, entry symbols,
entry-relative stack pointers, bounded aligned stack pointers, byte provenance,
call-invalidated data, and unknown values. Byte provenance allows partial
register saves/restores while modeling 32-bit zero extension.

Known constant operations and simple affine offsets are retained. Other
operations can weaken their result to unknown. Low-bit stack alignment remains
usable even when exact offsets are lost; known frame pointers can subsequently
recover the exact stack position.

A worklist propagates states to control-flow successors. Joins retain equal
facts and weaken differing facts; memory retains only agreeing cell ranges.
Origins are finite sets of source instruction locations. This domain converges
for loops; a defensive work limit reports a gap if exhausted.

Diagnostics are emitted after incoming states stabilize. This avoids findings
based on transient states during loop/branch exploration. Unknown branch
conditions explore both edges. Unrelated unknown values are never assumed equal.
No predicate solver is used; conditional paths can reduce preservation claims
to warnings. Reported source locations illustrate contributing operations, not
a proof that a route is executable.

## Stack memory and calls

Cells hold a byte width and a value at an entry-relative offset. Exact stores
replace overlapping cells; partial overlaps discard old whole-cell knowledge,
which can yield uncertainty when a later wider load occurs. Unknown stores
invalidate potentially aliased saves and the return-address slot.

Calls invalidate memory below the call-time stack pointer, make caller-saved
registers unknown, preserve callee-saved registers, and assume a clear DF on
normal return. Invalidated values are diagnosed when subsequently read, allowing
dead red-zone data and values recreated after calls.

Stack addresses in argument registers, outgoing candidate stack slots, or
previously exported locations expose tracked memory to a callee. Without
signatures or alias analysis, exposed cells become unknown conservatively.
This includes the possible return-address alias and can generate several
obligation warnings accompanied by one combined uncertainty gap per site.

## Result interpretation

An error identifies a contract violation using known abstract facts. A warning
identifies a possible violation. Missing facts that prevent a required check
also create an analysis gap. Unsupported instructions stop their path, while
other paths retain their results. Diagnostics are deduplicated, related locations
are combined, and output order is stable.

Coverage is per selected function, including inferred boundaries, reached
instructions, analyzed instructions, and reasons for incompleteness. A complete
analysis can still report violations or warnings such as unsafe red-zone reads.
Coverage never implies whole-ABI certification.
