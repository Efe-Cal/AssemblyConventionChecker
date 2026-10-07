setup_terminal:
    
    pushq    %rbp
    movq     %rsp, %rbp

    # tcgetattr to get the old termios settings
    movq    $0, %rdi                    # stdin file descriptor
    leaq    old_termios(%rip), %rsi
    call    tcgetattr


    movq    $0, %rdi                    # stdin file descriptor
    leaq    current_termios(%rip), %rsi
    call    tcgetattr

    # Setting ICANON (2) and ECHO (8) flag bits to 0 and keep the rest same
    andl     $~10, current_termios+12(%rip)

    mov     $0, %rdi
    mov     $0, %rsi
    leaq    current_termios(%rip), %rdx
    call    tcsetattr

    # Get current file control flags
    movq $0, %rdi                    # stdin
    movq $3, %rsi                    # 3:  F_GETFL
    call fcntl

    movq %rax, old_flags(%rip)

    movq %rax, %rdx
    orq $2048, %rdx                 # Add the non blocking flag (value 2048) to the flags we got

    # set file control flags
    movq $0, %rdi
    movq $4, %rsi                    # 4: F_SETFL
    call fcntl

    movq     %rbp, %rsp
    popq     %rbp

    ret

restore_terminal:
    
    pushq    %rbp
    movq     %rsp, %rbp

    mov     $0, %rdi
    mov     $0, %rsi
    mov     old_termios(%rip), %rdx
    call    tcsetattr
    
    movq $0, %rdi
    movq $4, %rsi                    # F_SETFL
    movq old_flags(%rip), %rdx
    call fcntl

    movq     %rbp, %rsp
    popq     %rbp

    ret


read_key:
    movq    $0, %rax            # sys_read
    movq    $0, %rdi            # stdin
    leaq    key(%rip), %rsi     # write location
    movq    $1, %rdx            # read 1 byte
    syscall

    # rax == 1 means a key was read
    cmpq $1, %rax
    jne no_key

    movzbl key(%rip), %eax
    ret
    
    no_key:
        mov     $0, %rax
        ret
