# Only needed for ~/.gdbinit setup
# source /path/to/pwndbg/gdbinit.py

set context-clear-screen on
set follow-fork-mode parent

# Make sure you source tuimux after pwndbg is loaded
source /path/to/tuimux/gdbinit.py
