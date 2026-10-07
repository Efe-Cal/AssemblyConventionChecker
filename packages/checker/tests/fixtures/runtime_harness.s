# Trusted Linux-only runtime checks; this file is intentionally not a checker fixture.
.text
.globl check_runtime
.type check_runtime,@function
check_runtime:
    pushq %rbp
    pushq %rbx
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15
    subq $8,%rsp
    # Even a zero-count 32-bit shift clears the register's upper half.
    movabsq $0x100000000,%rbx
    shll $0,%ebx
    testq %rbx,%rbx
    jne .Lfailed
    # A false 32-bit conditional move also clears the upper half.
    movabsq $0x100000000,%rbx
    xorl %eax,%eax
    testl %eax,%eax
    cmovnel %esi,%ebx
    testq %rbx,%rbx
    jne .Lfailed
    movq $11,%rbx
    movq $22,%rbp
    movq $33,%r12
    movq $44,%r13
    movq $55,%r14
    movq $66,%r15
    call good_frame
    testq %rax,%rax
    jne .Lfailed
    call good_leaf
    cmpq $11,%rbx
    jne .Lfailed
    cmpq $22,%rbp
    jne .Lfailed
    cmpq $33,%r12
    jne .Lfailed
    cmpq $44,%r13
    jne .Lfailed
    cmpq $55,%r14
    jne .Lfailed
    cmpq $66,%r15
    jne .Lfailed
    pushfq
    popq %rax
    testq $0x400,%rax
    jne .Lfailed
    call red_zone_live
    cmpq $123,%rax
    jne .Lfailed
    xorl %eax,%eax
    jmp .Ldone
.Lfailed:
    movl $1,%eax
.Ldone:
    cld
    addq $8,%rsp
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbx
    popq %rbp
    ret
.size check_runtime,.-check_runtime

.globl alignment_probe
.type alignment_probe,@function
alignment_probe:
    leaq 8(%rsp),%rax
    andq $15,%rax
    ret
.size alignment_probe,.-alignment_probe

.globl overwrite_red_zone
.type overwrite_red_zone,@function
overwrite_red_zone:
    movq $123,-8(%rsp)
    xorl %eax,%eax
    ret
.size overwrite_red_zone,.-overwrite_red_zone
.section .note.GNU-stack,"",@progbits
