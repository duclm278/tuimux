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
    di = view(ma_id, U, "70%", "disasm", follow="►")
    re = view(di.id, R, "50%", "regs")
    st = view(re.id, D, "35%", "stack")
    ba = view(ma_id, R, "25%", "backtrace", follow="►")
    co = view(di.id, D, "55%", "code", follow="►")

    mapping = {"regs": re, "legend": re, "disasm": di, "code": co, "stack": st, "backtrace": ba}
    for section, pane in mapping.items():
        contextoutput(section, pane.path, clearing=True, banner="none")
end
set context-backtrace-lines 128
set context-code-lines 32
set context-disasm-lines 32
set context-stack-lines 128
