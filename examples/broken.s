# Three independent ABI mistakes, with a source explanation for each.
.text
.globl example
.type example,@function
example:
    movq $42,%rbx
    call helper@PLT
    std
    ret
.size example,.-example
.section .note.GNU-stack,"",@progbits
