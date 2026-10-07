.data
clear_screen: .asciz "\033[2J\033[H"
print_snake_segment: .asciz "\33[%u;%uH@"
print_apple: .asciz "\33[%u;%uHA"

old_termios: .zero 64
current_termios: .zero 64
old_flags: .quad 0

.equ MAX_SNAKE, 400
.equ SIZE, 20


.bss
snake: .skip MAX_SNAKE * 2
apple: .skip 2

head_index: .quad 0
tail_index: .quad 0
direction: .byte 0

key: .byte 0

.text

.extern tcgetattr
.extern tcsetattr
.extern fcntl

.include "handle_movement.s"
.include "handle_terminal.s"


pressed_up:     
    movb    $0, direction(%rip)
    jmp continue_game_loop
pressed_right:  
    movb    $1, direction(%rip)
    jmp continue_game_loop
pressed_down:   
    movb    $2, direction(%rip)
    jmp continue_game_loop
pressed_left:   
    movb    $3, direction(%rip)
    jmp continue_game_loop

.globl main
main:

    pushq    %rbp
    movq     %rsp, %rbp

    call setup_terminal

    # setup random from time seed
    call time
    mov %rax, %rdi
    call srand

    leaq snake(%rip), %rbx
    # segment 0 = (5,5)
    movb $5, (%rbx)      # x
    movb $5, 1(%rbx)      # y

    # segment 1 = (6,5)
    movb $6, 2(%rbx)      # x
    movb $5, 3(%rbx)      # y

    # segment 2 = (7,5)
    movb $7, 4(%rbx)      # x
    movb $5, 5(%rbx)      # y

    # apple
    movb $15, apple(%rip)
    movb $5,  apple+1(%rip)

    movq $0, tail_index(%rip)
    movq $2, head_index(%rip)

    movb $1, direction(%rip)

    game_loop:
        call    read_key

        cmpb $'w', %al
        je pressed_up

        cmpb $'a', %al
        je pressed_left

        cmpb $'s', %al
        je pressed_down

        cmpb $'d', %al
        je pressed_right

        continue_game_loop:

        leaq    snake(%rip), %rbx
        # get current head 
        mov     head_index(%rip), %r12
        movb    (%rbx,%r12,2), %cl       # x
        movb    1(%rbx,%r12,2), %dl      # y

        movq    $0, %rdi

        cmpb    %cl, apple(%rip)
        jne     no_apple_eaten

        cmpb    %dl, apple+1(%rip)
        jne     no_apple_eaten

        movq    $1, %rdi

        push    %rbx
        call    relocate_apple
        pop     %rbx

        no_apple_eaten:
        call    handle_movement
        call    render_screen

        mov $500000, %rdi
        call usleep

        jmp game_loop

    call restore_terminal

    movq     %rbp, %rsp
    popq     %rbp

    mov $0, %rdi
    call exit

render_screen:
    pushq    %rbp
    movq     %rsp, %rbp

    mov     tail_index(%rip), %r12

    mov $clear_screen, %rdi
    mov $0, %rax
    call printf

    # Render apple
    leaq    apple(%rip), %rbx
    movb    (%rbx), %cl       # x
    movb    1(%rbx), %dl      # y
    
    mov $print_apple, %rdi
    movzbq %dl, %rsi
    movzbq %cl, %rdx
    mov $0, %rax
    call printf

    leaq    snake(%rip), %rbx
    render_snake_loop:

        movb    (%rbx,%r12,2), %cl       # x
        movb    1(%rbx,%r12,2), %dl      # y

        mov $print_snake_segment, %rdi
        movzbq %dl, %rsi
        movzbq %cl, %rdx
        mov $0, %rax
        call printf

        inc %r12
        cmp %r12, head_index(%rip)
        jge render_snake_loop

    # flush the stdin buffer 
    mov $0, %rdi       # NULL = flush all output streams
    call fflush
    
    movq     %rbp, %rsp
    popq     %rbp
    ret

relocate_apple:
    movq    $SIZE, %rbx

    call    rand
    divq    %rbx, %rax          # apple = rand_num % SIZE 
    inc     %dl
    movb    %dl, apple(%rip)

    call    rand
    divq    %rbx, %rax
    inc     %di
    movb    %dl, apple+1(%rip)
    
    ret
