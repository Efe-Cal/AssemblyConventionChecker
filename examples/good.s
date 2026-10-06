# A frame-pointer-free function that saves a callee-saved register across a call.
.text
.globl example
.type example,@function
example:
    pushq %rbx
    movq %rdi,%rbx
    call helper@PLT
    movq %rbx,%rax
    popq %rbx
    ret
.size example,.-example
.section .note.GNU-stack,"",@progbits
