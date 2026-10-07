# The value at -24(%rsp) is live across a call and may be overwritten.
.text
.globl example
.type example,@function
example:
    movq $99,-24(%rsp)
    subq $8,%rsp
    call helper@PLT
    addq $8,%rsp
    movq -24(%rsp),%rax
    ret
.size example,.-example
.section .note.GNU-stack,"",@progbits
