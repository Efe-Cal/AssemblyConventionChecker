# EXPECT: no diagnostics
.text
.globl good_frame
.type good_frame,@function
good_frame:
    pushq %rbp
    movq %rsp,%rbp
    pushq %rbx
    subq $8,%rsp
    movq $42,%rbx
    call alignment_probe
    addq $8,%rsp
    popq %rbx
    leave
    ret
.size good_frame,.-good_frame
.section .note.GNU-stack,"",@progbits
