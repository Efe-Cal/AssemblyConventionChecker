import json
from pathlib import Path
import unittest

from assembly_convention_checker import analyze
from assembly_convention_checker.parser import ParseError, constant

FIXTURES = Path(__file__).parent / "fixtures"


def function(body, metadata=True):
    return ".text\n.globl f\n" + (".type f,@function\n" if metadata else "") + "f:\n" + body + "\n" + (".size f,.-f\n" if metadata else "")


class CheckerTests(unittest.TestCase):
    def check(self, body, expected=(), **kwargs):
        report = analyze(function(body), filename="example.s", **kwargs)
        found = {(d.rule_id, d.category) for d in report.diagnostics}
        for finding in expected:
            self.assertIn(finding, found, report.to_dict())
        return report

    def clean(self, body):
        report = self.check(body)
        self.assertEqual([], report.diagnostics, report.to_dict())
        self.assertTrue(report.complete)
        return report

    def test_valid_functions(self):
        for body in (
            "movq %rdi,%rax\nret",
            "pushq %rbp\nmovq %rsp,%rbp\nsubq $16,%rsp\ncall g\nleave\nret",
            "pushq %rbx\nsubq $16,%rsp\nmovq $7,%rbx\ncall g\naddq $16,%rsp\npopq %rbx\nret",
            "subq $8,%rsp\ncall g\naddq $8,%rsp\nret",
            "movq %rbx,%rax\nxorq %rbx,%rbx\nmovq %rax,%rbx\nret",
            "addq $16,%rbx\nsubq $16,%rbx\nret",
            "movb %bl,%bl\nmovw %bx,%bx\nret",
        ):
            with self.subTest(body=body): self.clean(body)

    def test_core_errors(self):
        cases = {
            "call g\nret": "ABI_STACK_ALIGNMENT",
            "subq $8,%rsp\nret": "ABI_STACK_RESTORE",
            "movq $0,(%rsp)\nret": "ABI_RETURN_ADDRESS",
            "pushq %rbx\nmovq $0,(%rsp)\npopq %rbx\nret": "ABI_CALLEE_SAVED",
            "std\nret": "ABI_DIRECTION_FLAG",
            "subq $8,%rsp\nstd\ncall g\naddq $8,%rsp\nret": "ABI_DIRECTION_FLAG",
            "movb $1,-129(%rsp)\nret": "ABI_RED_ZONE_BOUNDS",
            "ret $8": "ABI_RET_CLEANUP",
        }
        for body, rule in cases.items():
            with self.subTest(body=body): self.check(body, [(rule, "error")])

    def test_wrong_and_overlapping_saves(self):
        for body, rule in (
            ("subq $16,%rsp\nmovq %rbx,(%rsp)\nxorq %rbx,%rbx\nmovq 8(%rsp),%rbx\naddq $16,%rsp\nret", "ABI_CALLEE_SAVED"),
            ("pushq %rbx\nmovb $0,2(%rsp)\npopq %rbx\nret", "ABI_CALLEE_SAVED"),
            ("movb $0,2(%rsp)\nret", "ABI_RETURN_ADDRESS"),
        ):
            self.check(body, [(rule, "warning")])

    def test_partial_and_32_bit_writes(self):
        for body in ("movb $0,%bl", "movw $0,%bx", "xorl %ebx,%ebx", "movl %ebx,%ebx"):
            with self.subTest(body=body): self.check(body + "\nret", [("ABI_CALLEE_SAVED", "error")])

    def test_instruction_size_suffixes(self):
        for suffix, register, size in (("b", "al", 1), ("w", "ax", 2), ("l", "eax", 4), ("q", "rax", 8)):
            for destination in (f"%{register}", f"-{size}(%rsp)"):
                with self.subTest(suffix=suffix, destination=destination):
                    self.clean(
                        f"mov{suffix} $3,{destination}\n"
                        f"and{suffix} $1,{destination}\n"
                        f"cmp{suffix} $1,{destination}\nje 1f\nstd\n1: ret"
                    )
            wrong_register = "%eax" if suffix == "q" else "%rax"
            for mnemonic in ("cmp", "and"):
                with self.subTest(suffix=suffix, mnemonic=mnemonic, mismatch=True):
                    report = self.check(f"{mnemonic}{suffix} $1,{wrong_register}\nret",
                        [("INPUT_INSTRUCTION", "error")])
                    self.assertTrue(report.input_errors)

    def test_all_preserved_registers(self):
        for name in ("rbx", "rbp", "r12", "r13", "r14", "r15"):
            self.clean(f"pushq %{name}\nxorq %{name},%{name}\npopq %{name}\nret")
            self.check(f"movq $0,%{name}\nret", [("ABI_CALLEE_SAVED", "error")])

    def test_call_clobbers_temporary_save(self):
        self.check("movq %rbx,%rax\nsubq $8,%rsp\ncall g\naddq $8,%rsp\nmovq %rax,%rbx\nret",
            [("ABI_CALLEE_SAVED", "warning"), ("ANALYSIS_UNKNOWN", "analysis_gap")])

    def test_indirect_calls(self):
        for target in ("*%rax", "*(%rax)"):
            self.clean(f"subq $8,%rsp\ncall {target}\naddq $8,%rsp\nret")

    def test_unmarked_internal_call(self):
        report = analyze(function("subq $8,%rsp\ncall helper\naddq $8,%rsp\nret\nhelper: ret", metadata=False))
        self.assertEqual(["f", "helper"], [f.name for f in report.functions])
        self.assertEqual([], report.diagnostics)
        self.assertTrue(self.check("call 1f\nret", [("INPUT_INSTRUCTION", "error")]).input_errors)

    def test_special_dot_branch(self):
        self.clean("std\njmp .")

    def test_direction_flag_restored(self): self.clean("std\ncld\nret")

    def test_join(self):
        report = self.check("testq %rdi,%rdi\nje 1f\nxorq %rbx,%rbx\n1: ret", [("ABI_CALLEE_SAVED", "warning")])
        self.assertFalse(report.complete)

    def test_multiple_returns(self):
        self.clean("testq %rdi,%rdi\nje 1f\nmovq $1,%rax\nret\n1: xorq %rax,%rax\nret")

    def test_known_condition_prunes(self): self.clean("xorl %eax,%eax\ntestl %eax,%eax\nje 1f\nstd\n1: ret")

    def test_unknown_values_are_not_assumed_equal(self):
        self.check("subq $8,%rsp\ncall g\naddq $8,%rsp\ncmpq %rax,%rdi\njne 1f\nret\n1: std\nret",
            [("ABI_DIRECTION_FLAG", "error")])
        self.clean("subq $8,%rsp\ncall g\naddq $8,%rsp\ncmpq %rax,%rax\njne 1f\nret\n1: std\nret")

    def test_adc_carry_overflow(self):
        self.clean("movb $255,%al\naddb $1,%al\nadcb $255,%al\njc 1f\nstd\n1: ret")

    def test_partial_register_save_restore(self):
        self.clean("movw %bx,-8(%rsp)\nmovw $0,%bx\nmovw -8(%rsp),%bx\nret")
        self.clean("movb %bh,-8(%rsp)\nmovb $0,%bh\nmovb -8(%rsp),%bh\nret")

    def test_32_bit_conditional_writes_zero_extend(self):
        self.check("xorq %rax,%rax\ntestq %rax,%rax\ncmovnel %esi,%ebx\nret", [("ABI_CALLEE_SAVED", "error")])
        self.check("shll $0,%ebx\nret", [("ABI_CALLEE_SAVED", "error")])

    def test_memory_shift_without_suffix(self):
        self.clean("subq $16,%rsp\nmovl $1,(%rsp)\nmovb $1,%cl\nshl %cl,(%rsp)\ncmpl $2,(%rsp)\nje 1f\nstd\n1: addq $16,%rsp\nret")

    def test_dead_malformed_instruction(self):
        self.assertTrue(self.check("jmp 1f\nmovq %eax,%rbx\n1: ret", [("INPUT_INSTRUCTION", "error")]).input_errors)
        self.assertTrue(self.check("xorq %rax,%rax\ntestq %rax,%rax\njne 1f\nret", [("INPUT_INSTRUCTION", "error")]).input_errors)

    def test_opaque_reversible_arithmetic_is_uncertain(self):
        report = self.check("notq %rbx\nnotq %rbx\nret", [("ABI_CALLEE_SAVED", "warning")])
        self.assertFalse(any(d.category == "error" for d in report.diagnostics))

    def test_wide_realignment_restoration(self):
        self.clean("pushq %rbp\nmovq %rsp,%rbp\nandq $-32,%rsp\ncall g\nleave\nret")

    def test_comment_columns_after_label(self):
        source = ".globl f\nf: /* padding */ std; ret\n"
        report = analyze(source)
        error = next(d for d in report.diagnostics if d.rule_id == "ABI_DIRECTION_FLAG")
        self.assertEqual(source.splitlines()[1].index("std") + 1, error.related_locations[0].column)

    def test_alignment_fill(self):
        self.clean(".p2align 4,,10\nret")
        self.clean(".p2align 4,0x90\nret")
        self.check(".p2align 4,0\nret", [("ANALYSIS_UNSUPPORTED", "analysis_gap")])

    def test_loops(self):
        self.clean("1: pushq %rbx\nxorq %rbx,%rbx\npopq %rbx\ndecq %rdi\njne 1b\nret")
        self.clean("movq $3,%rcx\n1: nop\nloop 1b\nret")
        report = self.check("1: subq $8,%rsp\ndecq %rdi\njne 1b\nret", [("ABI_STACK_RESTORE", "warning")])
        self.assertLess(len(report.diagnostics), 10)

    def test_counter_branch(self): self.clean("movq $0,%rcx\njrcxz 1f\nstd\n1: ret")

    def test_unknown_stack_adjustment(self): self.check("subq %rax,%rsp\nret", [("ABI_STACK_RESTORE", "warning")])

    def test_realignment_restoration(self): self.clean("pushq %rbp\nmovq %rsp,%rbp\nandq $-16,%rsp\ncall g\nleave\nret")

    def test_unsupported_path(self):
        report = self.check("testq %rdi,%rdi\nje 1f\nsyscall\nret\n1: std\nret",
            [("ANALYSIS_UNSUPPORTED", "analysis_gap"), ("ABI_DIRECTION_FLAG", "error")])
        self.assertFalse(report.complete)

    def test_unreachable_unsupported(self):
        report = self.clean("jmp 1f\nsyscall\n1: ret")
        self.assertEqual(2, report.functions[0].reachable_instructions)

    def test_red_zone_lifetimes(self):
        for body in (
            "movq %rbx,-128(%rsp)\nxorq %rbx,%rbx\nmovq -128(%rsp),%rbx\nret",
            "movq $1,-8(%rsp)\nsubq $8,%rsp\ncall g\naddq $8,%rsp\nret",
            "movq $1,-24(%rsp)\nsubq $8,%rsp\ncall g\naddq $8,%rsp\nmovq $2,-24(%rsp)\nmovq -24(%rsp),%rax\nret",
            "subq $24,%rsp\nmovq %rbx,8(%rsp)\nxorq %rbx,%rbx\ncall g\nmovq 8(%rsp),%rbx\naddq $24,%rsp\nret",
        ):
            with self.subTest(body=body): self.clean(body)
        self.check("movq $1,-24(%rsp)\nsubq $8,%rsp\ncall g\naddq $8,%rsp\nmovq -24(%rsp),%rax\nret", [("ABI_RED_ZONE_LIVE", "warning")])

    def test_address_tracking(self):
        self.clean("subq $16,%rsp\nxorq %rax,%rax\nmovq %rbx,8(%rsp,%rax,1)\nxorq %rbx,%rbx\nmovq 8(%rsp,%rax,1),%rbx\naddq $16,%rsp\nret")
        self.clean("leaq -8(%rsp),%rax\nmovq %rbx,(%rax)\nxorq %rbx,%rbx\nmovq (%rax),%rbx\nret")

    def test_aliases_and_escape(self):
        self.check("pushq %rbx\nmovq $1,(%rdi)\npopq %rbx\nret", [("ABI_CALLEE_SAVED", "warning"), ("ABI_RETURN_ADDRESS", "warning")])
        self.check("pushq %rbx\nleaq (%rsp),%rdi\ncall g\npopq %rbx\nret", [("ABI_CALLEE_SAVED", "warning")])

    def test_tails(self):
        self.clean("jmp external_function")
        self.check("subq $8,%rsp\njmp external_function", [("ABI_STACK_RESTORE", "error"), ("ABI_STACK_ALIGNMENT", "error")])
        self.check("jmp *%rax", [("ANALYSIS_UNSUPPORTED", "analysis_gap")])
        source = function("jmp g") + ".globl g\n.type g,@function\ng: ret\n.size g,.-g\n"
        report = analyze(source, entries=("f",))
        self.assertEqual([], report.diagnostics)
        self.assertEqual(["f"], [f.name for f in report.functions])

    def test_ret_zero_cleanup(self): self.clean("ret $0")

    def test_implicit_destinations(self):
        for instruction in ("mulq %rdi", "imulq %rdi", "divq %rdi", "idivq %rdi", "cqto"):
            with self.subTest(instruction=instruction):
                self.check(f"movq %rbx,%rdx\n{instruction}\nmovq %rdx,%rbx\nret", [("ABI_CALLEE_SAVED", "warning")])

    def test_extensions_and_conditions(self):
        self.clean("movl $-1,%eax\ncltq\nmovzbq %dil,%rax\nmovswq %si,%rax\nret")
        self.clean("movq %rbx,%rax\ntestq %rdi,%rdi\ncmoveq %rax,%rbx\nret")
        self.check("testq %rdi,%rdi\ncmoveq %rsi,%rbx\nret", [("ABI_CALLEE_SAVED", "warning")])
        self.clean("testq %rdi,%rdi\nsete %al\nshlq $1,%rax\nshrq %cl,%rax\nrolq $3,%rax\nret")
        self.check("setne %bl\nret", [("ABI_CALLEE_SAVED", "error")])

    def test_xchg(self):
        self.clean("xchgq %rax,%rbx\nxchgq %rax,%rbx\nret")
        self.clean("pushq %rbx\nxorq %rbx,%rbx\nxchgq (%rsp),%rbx\naddq $8,%rsp\nret")

    def test_numeric_labels(self):
        self.clean("jmp 1f\n1: nop\njmp 1f\n1: ret")
        for target in ("1f", ".Lmissing"):
            self.assertTrue(self.check(f"jmp {target}", [("INPUT_INSTRUCTION", "error")]).input_errors)

    def test_comments_locations(self):
        source = '.globl f\nf: /* comment */ nop; std; ret # comment\n'
        report = analyze(source, filename="snippet.s")
        finding = next(d for d in report.diagnostics if d.rule_id == "ABI_DIRECTION_FLAG")
        self.assertEqual(("snippet.s", 2, source.splitlines()[1].index("ret") + 1), (finding.location.filename, finding.location.line, finding.location.column))
        self.assertTrue(finding.related_locations)

    def test_constants(self):
        source = ".equ FRAME, (2 << 2)\n.set SIZE,FRAME\n" + function("subq $SIZE,%rsp\ncall g\naddq $SIZE,%rsp\nret")
        self.assertEqual([], analyze(source).diagnostics)
        for expression, expected in (("010", 8), ("0x10", 16), (".size_const", 8)):
            self.assertEqual(expected, constant(expression, {".size_const": 8}))
        with self.assertRaises(ParseError): constant("__import__('os')", {})
        self.check("subq $FORWARD,%rsp\nret", [("ABI_STACK_RESTORE", "warning")])

    def test_metadata_aliases_and_inference(self):
        source = ".globl alias\n.type f,@function\nf:\nalias:\n.cfi_startproc\nret\n.cfi_endproc\n.size f,.-f\n"
        report = analyze(source)
        self.assertEqual([], report.diagnostics)
        self.assertEqual(["alias", "f"], report.functions[0].aliases)
        self.assertFalse(report.functions[0].boundaries_inferred)
        self.assertTrue(analyze(function("ret", metadata=False)).functions[0].boundaries_inferred)
        self.assertEqual([], analyze("bare: ret\n", entries=("bare",)).diagnostics)
        self.assertTrue(analyze("bare: ret\n", entries=("absent",)).input_errors)

    def test_no_entry(self):
        report = analyze("bare: ret\n")
        self.assertFalse(report.complete)
        self.assertIn("ANALYSIS_NO_ENTRY", [d.rule_id for d in report.diagnostics])

    def test_cfi_is_metadata(self): self.check("movq $0,%rbx\n.cfi_restore %rbx\nret", [("ABI_CALLEE_SAVED", "error")])

    def test_conflicting_boundaries(self):
        report = analyze(".globl f\nf:\n.cfi_startproc\nret\n.cfi_endproc\nnop\n.size f,.-f\n")
        self.assertIn("ANALYSIS_BOUNDARY", [d.rule_id for d in report.diagnostics])
        self.assertFalse(report.functions[0].complete)
        report = analyze(".size f,.-f\n.globl g\ng: ret\n.globl f\nf: ret\n")
        self.assertFalse(report.complete)
        self.assertTrue(all(f.total_instructions >= 0 for f in report.functions))

    def test_fallthrough(self): self.check("nop", [("ANALYSIS_FALLTHROUGH", "analysis_gap")])

    def test_structural_directives(self):
        for prefix in (".macro save\n.endm\n", '.include "x.s"\n', ".if 1\n.endif\n", "#include <x.h>\n"):
            report = analyze(prefix + function("ret"))
            self.assertEqual([], report.functions)
            self.assertIn("ANALYSIS_EXPANSION", [d.rule_id for d in report.diagnostics])

    def test_modes_data_and_registers(self):
        for prefix in (".intel_syntax noprefix", ".code32"):
            report = analyze(prefix + "\n" + function("ret"))
            self.assertFalse(report.complete)
            self.assertEqual([], report.functions)
        self.check(".byte 0xc3", [("ANALYSIS_UNSUPPORTED", "analysis_gap")])
        self.check("movq %xmm0,%rax\nret", [("ANALYSIS_UNSUPPORTED", "analysis_gap")])
        self.assertEqual([], analyze(function("ret") + '.section .rodata\nmessage: .asciz "hello; # world"\n').diagnostics)

    def test_malformed_source(self):
        for body in ("movq %rax,", "movq 8(%rsp,%rax,3),%rax", "movq (%rsp,%rax,%rcx),%rax", "movq %nosuch,%rax", "movq ((%rsp),%rax"):
            with self.subTest(body=body): self.assertTrue(analyze(function(body)).input_errors)

    def test_malformed_forms(self):
        for body in ("movq %eax,%rbx", "addq (%rax),(%rcx)", "call %rax", "popq $1", "pushl %eax", "cmoveb %al,%bl", "ret %rax"):
            with self.subTest(body=body): self.assertTrue(analyze(function(body)).input_errors)

    def test_process_entry(self):
        report = analyze(".globl _start\n_start: ret\n")
        self.assertFalse(report.complete)
        self.assertIn("ANALYSIS_ENTRY_CONVENTION", [d.rule_id for d in report.diagnostics])

    def test_determinism(self):
        source = function("testq %rdi,%rdi\nje 1f\nstd\n1: ret")
        self.assertEqual(json.dumps(analyze(source).to_dict()), json.dumps(analyze(source).to_dict()))

    def test_fixtures(self):
        for path in sorted(FIXTURES.glob("*.s")):
            with self.subTest(path=path.name):
                source = path.read_text()
                if not source.startswith("# EXPECT:"):
                    continue
                report = analyze(source, filename=path.name)
                expectation = source.splitlines()[0].removeprefix("# EXPECT: ")
                if expectation == "no diagnostics":
                    self.assertEqual([], report.diagnostics, report.to_dict())
                    self.assertTrue(report.complete)
                else:
                    rule, category, _, line = expectation.replace(",", "").split()
                    self.assertIn((rule, category, int(line)), [(d.rule_id, d.category, d.location.line) for d in report.diagnostics])


if __name__ == "__main__": unittest.main()
