# Only needed for ~/.gdbinit setup
# source /path/to/pwndbg/gdbinit.py

set context-clear-screen on
set follow-fork-mode parent

# Make sure you source tuimux after pwndbg is loaded
source /path/to/tuimux/gdbinit.py
python
def design():
    ma_id = os.environ["TMUX_PANE"]
    call(f'tmux select-pane -T "main" -t {ma_id}')

    L, D, U, R = "-hb", "-v", "-vb", "-h"
    ba = view(ma_id, R, "25%", "backtrace", wrap=False, follow="►")
    di = view(ma_id, U, "80%", "disasm", wrap=False, follow="►")
    io = tmux(di.id, R, "55%", "input/output", cmd="tty; tail -f /dev/null")
    st = view(io.id, U, "75%", "stack", wrap=False)
    re = view(st.id, U, "60%", "regs", wrap=False)
    ip = tmux(ba.id, D, "30%", "ipython", cmd="ipython3")
    co = view(di.id, D, "55%", "code", wrap=False, follow="►")

    mapping = {"regs": re, "legend": re, "disasm": di, "code": co, "stack": st, "backtrace": ba}
    for section, pane in mapping.items():
        contextoutput(section, pane.path, clearing=True, banner="none")
end
set context-backtrace-lines 128
set context-code-lines 32
set context-disasm-lines 32
set context-stack-lines 128
python
def design():
    ma_id = os.environ["TMUX_PANE"]
    call(f'tmux select-pane -T "main" -t {ma_id}')

    L, D, U, R = "-hb", "-v", "-vb", "-h"
    ba = view(ma_id, R, "25%", "backtrace", wrap=False, follow="►")
    di = view(ma_id, U, "80%", "disasm", wrap=False, follow="►")
    io = tmux(di.id, R, "55%", "input/output", cmd="tty; tail -f /dev/null")
    st = view(io.id, U, "75%", "stack", wrap=False)
    re = view(st.id, U, "60%", "regs", wrap=False)
    ip = tmux(ba.id, D, "30%", "ipython", cmd="ipython3")
    co = view(di.id, D, "55%", "code", wrap=False, follow="►")

    mapping = {"regs": re, "legend": re, "disasm": di, "code": co, "stack": st, "backtrace": ba}
    for section, pane in mapping.items():
        contextoutput(section, pane.path, clearing=True, banner="none")
end
set context-backtrace-lines 128
set context-code-lines 32
set context-disasm-lines 32
set context-stack-lines 128
