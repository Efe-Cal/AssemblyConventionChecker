.data
percent_sign: .asciz "%"
minus_sign: .asciz "-"
dot: .asciz "."
hello: .asciz "%d %d %u %u %u %u %f %% \n"

.text

.globl main
main:
    pushq   %rbp
    movq    %rsp, %rbp

    movq    $hello, %rdi
    movq    $-1, %rsi
    movq    $-2, %rdx
    movq    $3, %rcx
    movq    $4, %r8
    movq    $5, %r9
    pushq   $0b00110011
    pushq   $6
    call    my_printf
      
    movq	%rbp, %rsp		
	popq	%rbp

    movq    $60, %rax 
    movq    $0, %rdi
    syscall


/* args:
    (RDI)   String: format string
    (RSI)   Any:    format value
    (RDX)   Any:    format value
    (RCX)   Any:    format value
    (R8)    Any:    format value
    (R9)    Any:    format value
    (Stack) Any:    format values
*/
my_printf:
    pushq   %rbp
    movq    %rsp, %rbp

    pushq   %r9         # push all parameters to stack
    pushq   %r8
    pushq   %rcx
    pushq   %rdx
    pushq   %rsi

    pushq   %r12
    pushq   %r13
    pushq   %r14
    pushq   %r15
    pushq   %rbx
    
    movq    %rdi, %rbx  # root string adress

    movq    $0, %r12    # print frame start
    movq    $0, %r13    # print frame end

    movq    $0, %r14    # format specifier counter

    jmp     search_format_loop

    percent_format:             # occurs when "%%"
        movq    %rbx, %rdi
        movq    %r12, %rsi
        movq    %r13, %rdx
        call    print_substring

        movq    $percent_sign, %rdi
        call    simple_print_string

        addq    $2, %r13        # skip 2 characters
        movq    %r13, %r12

        jmp     search_format_loop

    string_format:              # occurs when "%s"
        movq    %rbx, %rdi
        movq    %r12, %rsi
        movq    %r13, %rdx
        call    print_substring

        movq    %r14, %rdi
        call    get_specifier_value
        inc     %r14

        movq    %rax, %rdi
        call    simple_print_string

        addq    $2, %r13        # skip 2 characters
        movq    %r13, %r12
        
        jmp     search_format_loop

    unsigned_int_format:        # occurs when "%u"
        movq    %rbx, %rdi
        movq    %r12, %rsi
        movq    %r13, %rdx
        call    print_substring
        
        movq    %r14, %rdi
        call    get_specifier_value
        inc     %r14
        
        movq    %rax, %rdi
        call    print_unsigned_integer

        addq    $2, %r13        # skip 2 characters
        movq    %r13, %r12
        
        jmp     search_format_loop

    
    singed_int_format:
        movq    %rbx, %rdi
        movq    %r12, %rsi
        movq    %r13, %rdx
        call    print_substring
        
        movq    %r14, %rdi
        call    get_specifier_value
        inc     %r14

        cmpq    $0, %rax
        jge     print_positive_integer

        flip_negative_int:
            imulq   $-1, %rax
            movq    $minus_sign, %rdi
            pushq   %rax                # align stack (bruh who is writing these submit checks)
            pushq   %rax                # preserve rax
            call    simple_print_string
            popq    %rax                # get rax back
            popq    %rax

        print_positive_integer:
            movq    %rax, %rdi
            call    print_unsigned_integer

        addq    $2, %r13        # skip 2 characters
        movq    %r13, %r12
        
        jmp     search_format_loop

    
    float_format:               # S EEEE MMM occurs when "%f"
        movq    %rbx, %rdi
        movq    %r12, %rsi
        movq    %r13, %rdx
        call    print_substring
        
        movq    %r14, %rdi
        call    get_specifier_value
        inc     %r14

        cmpq    $128, %rax
        jl      print_unsigned_integer_part
        
        print_negative_sign:
            movq    $minus_sign, %rdi
            pushq   %rax                # preserve rax
            pushq   %rax
            call    simple_print_string
            popq    %rax                # get rax back
            popq    %rax 

        print_unsigned_integer_part:
            movq    %rax, %rcx
            andq    $0b01111000, %rcx   # exponent -> RCX
            shr     $3, %rcx

            movq    %rax, %r9
            andq    $0b00000111, %r9    # mantissa -> R9
            addq    $8, %r9

            shlq    %cl, %r9
            movq    %r9, %r10
            andq    $1023, %r10         # R10 for the fractional part (first 9 bits)
            
            shrq    $10, %r9            # 7 for excess-7 and 3 for mantissa size

            pushq   %r10                # save r10 from submit script
            pushq   %r10

            movq    %r9, %rdi
            call    print_unsigned_integer

        # print dot
        movq    $dot, %rdi 
        call    simple_print_string

        popq    %r10                # get back r10
        popq    %r10

        movq    $4, %r15            # set up loop counter for 3 decimal places + 1 round digit

        pushq   $0

       read_fraction_loop:
            imulq   $10, %r10           # multiply by 10 to expose next digit to division

            movq    %r10, %rdi
            shrq    $10, %rdi           # divide by 1024 and get next digit

            andq    $1023, %r10         # get remainder

            pushq   %rdi

            dec     %r15

            cmpq    $0, %r15
            jne     read_fraction_loop

        popq    %rdi

        movq    $3, %r15

        cmpq    $5, %rdi
        jl      print_fraction_loop

        inc     (%rsp)

        movq    $3, %r15
        print_fraction_loop:
            movq    -8(%rsp, %r15, 8), %rdi
            call    print_unsigned_integer

            dec     %r15

            cmpq    $0, %r15
            jne     print_fraction_loop

        popq    %rax
        popq    %rax
        popq    %rax
        popq    %rax

        addq    $2, %r13        # skip 2 characters
        movq    %r13, %r12
        
        jmp     search_format_loop

    
    test_format_specifier:
        cmpb    $'%' , 1(%rbx, %r13, 1)
        je      percent_format
        
        cmpb    $'s' , 1(%rbx, %r13, 1)
        je      string_format

        cmpb    $'u' , 1(%rbx, %r13, 1)
        je      unsigned_int_format

        cmpb    $'d' , 1(%rbx, %r13, 1)
        je      singed_int_format

        cmpb    $'f' , 1(%rbx, %r13, 1)
        je      float_format

        inc     %r13

    search_format_loop:
        cmpb    $'%' , (%rbx, %r13, 1)
        je      test_format_specifier

        inc     %r13
    
        cmpb    $0, -1(%rbx, %r13, 1)
        jne     search_format_loop

    dec     %r13
    movq    %rbx, %rdi
    movq    %r12, %rsi
    movq    %r13, %rdx
    call    print_substring

    popq    %rbx
    popq    %r15
    popq    %r14
    popq    %r13
    popq    %r12

    movq    %rbp, %rsp
    popq    %rbp
    ret


/* args:
    (RDI) Int: integer to print
*/
print_unsigned_integer:
    pushq   %rbp
    movq    %rsp, %rbp

    pushq   %r12
    pushq   %r13

    movq    %rdi, %rax

    movq    $0, %r13        # loop counter
    movq    $10, %r12       # divisor

    extract_digit_loop:
        movq    $0, %rdx    # reset remainder
        divq    %r12
        addq    $48, %rdx
        pushq   %rdx

        inc     %r13

        cmp     $0, %rax
        jg      extract_digit_loop

    read_and_write_digits:
        movq    $1, %rax                # syscall for sys_write
        movq    $1, %rdi                # writing target: stdout
        movq    %rsp, %rsi
        movq    $1, %rdx
        syscall

        addq    $8, %rsp
        dec     %r13

        cmp     $0, %r13
        jg      read_and_write_digits

    popq   %r13
    popq   %r12

    movq    %rbp, %rsp
    popq    %rbp
    ret


/* args:
    (RDI) Int: specifier value index
*/
get_specifier_value:
    pushq   %rbp
    movq    %rsp, %rbp

    pushq   %r12

    movq    (%rbp), %r12

    cmpq    $5, %rdi
    jl      get_from_register
    
    movq    -24(%r12, %rdi, 8), %rax
    jmp     return_specifier

    get_from_register:
        movq    -40(%r12, %rdi, 8), %rax

    return_specifier:
        popq    %r12

        movq    %rbp, %rsp
        popq    %rbp
        ret



/* args:
    (RDI) String: address of the string to be printed
*/
simple_print_string:
    pushq   %rbp
    movq    %rsp, %rbp

    mov     $0, %rdx
    count_string_length_loop:
        inc     %rdx
        
        cmpb    $0, (%rdi, %rdx, 1)
        jne     count_string_length_loop
    
    movq    %rdi, %rsi
    movq    $1, %rax                # syscall for sys_write
    movq    $1, %rdi                # writing target: stdout
    syscall
    
    movq    %rbp, %rsp
    popq    %rbp
    ret


/* args:
    (RDI) String: address of the string to be printed
    (RSI) Int:    start index of substring
    (RDX) Int:    end index of substring
*/
print_substring:
    pushq   %rbp
    movq    %rsp, %rbp

    subq    %rsi, %rdx              # rdx holds the string length

    addq    %rdi, %rsi
    
    movq    $1, %rax                # syscall for sys_write
    movq    $1, %rdi                # writing target: stdout
    syscall

    movq    %rbp, %rsp
    popq    %rbp
    ret
