# EXPECT: no diagnostics
.text
.globl good_leaf
.type good_leaf,@function
good_leaf:
    movq %rbx,-8(%rsp)
    movq $42,%rbx
    movq -8(%rsp),%rbx
    ret
.size good_leaf,.-good_leaf
.section .note.GNU-stack,"",@progbits
