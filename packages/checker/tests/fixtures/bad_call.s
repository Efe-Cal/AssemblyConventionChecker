# EXPECT: ABI_STACK_ALIGNMENT error, line 6
.text
.globl bad_call
.type bad_call,@function
bad_call:
    call external_function
    ret
.size bad_call,.-bad_call
