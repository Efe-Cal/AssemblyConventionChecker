# EXPECT: ABI_RED_ZONE_LIVE warning, line 10
.text
.globl red_zone_live
.type red_zone_live,@function
red_zone_live:
    movq $99,-24(%rsp)
    subq $8,%rsp
    call overwrite_red_zone
    addq $8,%rsp
    movq -24(%rsp),%rax
    ret
.size red_zone_live,.-red_zone_live
.section .note.GNU-stack,"",@progbits
