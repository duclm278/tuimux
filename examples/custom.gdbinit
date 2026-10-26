# Only needed for ~/.gdbinit setup
# source /path/to/pwndbg/gdbinit.py

set context-clear-screen on
set follow-fork-mode parent

# Make sure you source tuimux after pwndbg is loaded
source /path/to/tuimux/gdbinit.py
python
from tuimux import *
@layout(opts={
    "context-code-lines": 1024,
    "context-stack-lines": 1024,
    "context-disasm-lines": 1024,
})
def custom(ma):
    ba = view(ma, R, "25%", "backtrace", follow="►")
    di = view(ma, U, "80%", "disasm", follow="►")
    io = area(di, R, "55%", "input/output", cmd="tty; tail -f /dev/null")
    st = view(io, U, "75%", "stack")
    re = view(st, U, "60%", "regs").add("legend")
    ip = area(ba, D, "30%", "ipython", cmd="ipython3")
    co = view(di, D, "55%", "code", follow="►")
    ba.add("threads", banner="top")
end
tuimux layout custom -y
