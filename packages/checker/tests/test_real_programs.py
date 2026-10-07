"""Regression cases from the user's printf and Snake programs."""
from pathlib import Path
import unittest

from assembly_convention_checker import analyze
from assembly_convention_checker.parser import constant

FIXTURES = Path(__file__).parent / "fixtures" / "user_programs"


def load_include(name, parent):
    path = (Path(parent).parent / name).resolve()
    return str(path), path.read_text(encoding="utf-8-sig")


class RealProgramTests(unittest.TestCase):
    def test_printf_is_clean(self):
        source = (FIXTURES / "custom_printf.s").read_text()
        report = analyze(source)
        self.assertEqual([], report.diagnostics)
        self.assertTrue(report.complete)
        self.assertEqual(["main", "my_printf", "print_unsigned_integer", "get_specifier_value",
                          "simple_print_string", "print_substring"], [f.name for f in report.functions])

    def test_printf_broken_saves_and_loop_cleanup_are_detected(self):
        source = (FIXTURES / "custom_printf.s").read_text()
        for old, new, rule in (
            ("popq   %r12", "popq   %rax", "ABI_CALLEE_SAVED"),
            ("popq    %rbx", "popq    %rax", "ABI_CALLEE_SAVED"),
            ("        popq    %rax\n        popq    %rax\n        popq    %rax\n        popq    %rax",
             "        addq $24, %rsp", "ABI_STACK_ALIGNMENT"),
        ):
            with self.subTest(rule=rule, old=old):
                self.assertIn(old, source)
                report = analyze(source.replace(old, new, 1))
                self.assertTrue(any(d.rule_id == rule for d in report.diagnostics), report.to_dict())

    def test_snake_discovers_included_and_address_taken_functions(self):
        path = FIXTURES / "snake" / "main.s"
        report = analyze(path.read_text(), filename=str(path), include_loader=load_include)
        self.assertFalse(report.input_errors)
        self.assertEqual(["move_up", "move_down", "move_right", "move_left", "handle_movement",
                          "setup_terminal", "restore_terminal", "read_key", "main", "render_screen",
                          "relocate_apple"], [f.name for f in report.functions])
        self.assertTrue(all(f.reachable_instructions > 0 for f in report.functions))
        alignments = {(Path(d.location.filename).name, d.location.line) for d in report.diagnostics
                      if d.rule_id == "ABI_STACK_ALIGNMENT" and d.category == "error"}
        self.assertEqual({("main.s", 116), ("main.s", 184), ("main.s", 189), ("handle_movement.s", 56)}, alignments)
        for name in ("render_screen", "relocate_apple", "handle_movement"):
            self.assertTrue(any(d.function == name and d.rule_id == "ABI_CALLEE_SAVED" for d in report.diagnostics))
        self.assertFalse(any(d.rule_id in ("ANALYSIS_EXPANSION", "ANALYSIS_UNSUPPORTED") for d in report.diagnostics))

    def test_characters_are_not_comments_separators_or_operand_delimiters(self):
        for expression, expected in (("'w'", 119), ("'w", 119), ("'%'", 37), (r"'\n'", 10),
                                     (r"'\123'", 83), ("'a'+1", 98), ("'#'", 35), ("'/'", 47),
                                     ("';'", 59), ("','", 44), ("'('", 40), ('\'"\'', 34), (r"'\''", 39)):
            with self.subTest(expression=expression):
                self.assertEqual(expected, constant(expression, {}))
                source = f".globl f\nf: movb ${expression}, %al; cmpb ${expected}, %al; je 1f; std\n1: ret\n"
                self.assertEqual([], analyze(source).diagnostics)

    def test_shared_blocks_before_and_after_function_region_are_checked(self):
        for source in (
            "block: std; jmp done\n.globl f\nf: jmp block\ndone: ret\n",
            ".globl f,g\nf: jmp shared\ng: ret\nshared: std; ret\n",
            ".globl f\nf: jmp shared\n.size f,.-f\nshared: std; ret\n",
        ):
            report = analyze(source, entries=("f",))
            self.assertTrue(any(d.rule_id == "ABI_DIRECTION_FLAG" for d in report.diagnostics))
            self.assertFalse(any(d.rule_id == "ANALYSIS_UNSUPPORTED" for d in report.diagnostics))
            self.assertLessEqual(report.functions[0].reachable_instructions, report.functions[0].total_instructions)

    def test_syscalls_preserve_stack_and_saved_registers_but_clobber_rcx_r11(self):
        for number in (0, 1):
            report = analyze(f".globl f\nf: pushq %rbx; subq $8,%rsp; movq %rsp,%rsi; movq $8,%rdx; movq ${number},%rax; syscall; addq $8,%rsp; popq %rbx; ret")
            self.assertEqual([], report.diagnostics)
        for register in ("rcx", "r11"):
            report = analyze(f".globl f\nf: movq %rbx,%{register}; movq $1,%rax; syscall; movq %{register},%rbx; ret")
            self.assertTrue(any(d.rule_id == "ABI_CALLEE_SAVED" for d in report.diagnostics))
        report = analyze(".globl f\nf: movq %rsp,%rsi; movq $8,%rdx; movq $0,%rax; syscall; ret")
        self.assertTrue(any(d.rule_id == "ABI_RETURN_ADDRESS" for d in report.diagnostics))
        self.assertEqual([], analyze(".globl f\nf: subq $8,%rsp; movq $60,%rax; syscall").diagnostics)
        self.assertFalse(analyze(".globl f\nf: movq $999,%rax; syscall; ret").complete)

    def test_global_stores_do_not_invalidate_stack_saves(self):
        for store in ("movq $1,global(%rip)", "movq $1,global", "leaq global(%rip),%rax; movq $1,(%rax)"):
            report = analyze(f".data\nglobal: .quad 0\n.text\n.globl f\nf: pushq %rbx; {store}; popq %rbx; ret")
            self.assertEqual([], report.diagnostics)

    def test_countdown_stack_loops_still_check_calls_and_exits(self):
        good = ".globl f\nf: movq $4,%r10\nloop_start: pushq %rax; decq %r10; cmpq $0,%r10; jne loop_start\naddq $32,%rsp; ret"
        self.assertEqual([], analyze(good).diagnostics)
        report = analyze(good.replace("$32,%rsp", "$24,%rsp"))
        self.assertTrue(any(d.rule_id == "ABI_STACK_RESTORE" and d.category == "error" for d in report.diagnostics))
        report = analyze(good.replace("pushq %rax", "pushq %rax; call external"))
        self.assertTrue(any(d.rule_id == "ABI_STACK_ALIGNMENT" for d in report.diagnostics))

    def test_gnu_two_operand_division_validates_the_implicit_accumulator(self):
        for instruction in ("divq %rbx,%rax", "idivl %ebx,%eax", "divb %bl,%al"):
            self.assertEqual([], analyze(f".globl f\nf: {instruction}; ret").diagnostics)
        self.assertTrue(analyze(".globl f\nf: divq %rbx,%rcx; ret").input_errors)

    def test_negative_stack_index_checks_red_zone_bounds(self):
        source = ".globl f\nf: movq $-136,%rax; movq %rbx,(%rsp,%rax); ret"
        self.assertTrue(any(d.rule_id == "ABI_RED_ZONE_BOUNDS" for d in analyze(source).diagnostics))
        self.assertEqual([], analyze(source.replace("-136", "-128")).diagnostics)

    def test_include_errors_keep_the_include_site(self):
        for loader in (
            lambda name, parent: (parent, '.include "again.s"'),
            lambda name, parent: (_ for _ in ()).throw(OSError("missing source")),
        ):
            report = analyze('\n.include "missing.s"\n.globl f\nf: ret', filename="main.s", include_loader=loader)
            self.assertTrue(report.input_errors)
            self.assertEqual("INPUT_INCLUDE", report.diagnostics[0].rule_id)
            self.assertEqual(("main.s", 2), (report.diagnostics[0].location.filename, report.diagnostics[0].location.line))


if __name__ == "__main__":
    unittest.main()
