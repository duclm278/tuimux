import atexit
import os
from dataclasses import dataclass
from pathlib import Path
from subprocess import run

import gdb

try:
    from pwndbg.commands.context import contextoutput
except ImportError:
    exit()


@dataclass
class Pane:
    id: str
    path: str
    toggleable: bool = False


HERE = Path(__file__).resolve().parent
VIEW = f'python3 {HERE / "fifo.py"}'
panes = []
fifos = []


def call(cmd):
    return run(cmd, capture_output=True, shell=True, text=True).stdout.strip()


def tmux(target, direction, size, title, cmd="cat -"):
    pane_id, pane_tty = call(
        f'tmux split-window {direction} -t {target} -l {size} -P -F "#{{pane_id}}:#{{pane_tty}}" -d "{cmd}"'
    ).split(":", 1)
    pane = Pane(id=pane_id, path=pane_tty)
    panes.append(pane)
    call(f'tmux select-pane -T "{title}" -t {pane_id}')
    return pane


def view(target, direction, size, title, wrap=True, mouse=True, follow=None):
    fifo = f"/tmp/view-{os.getpid()}-{title}.fifo"
    os.mkfifo(fifo)
    fifos.append(fifo)
    cmd = f"{VIEW} {fifo} {title}"
    if wrap:
        cmd += " --wrap"
    if mouse:
        cmd += " --mouse"
    if follow:
        cmd += f" --follow {follow}"
    pane = tmux(target, direction, size, title, cmd=cmd)
    pane.path = fifo
    pane.toggleable = True
    return pane


class WrapCommand(gdb.Command):
    def __init__(self):
        super().__init__("wrap", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        for p in panes:
            if p.toggleable:
                call(f"tmux send-keys -t {p.id} w")


# fmt: off
def design():
    ma_id = os.environ["TMUX_PANE"]
    call(f'tmux select-pane -T "main" -t {ma_id}')

    L, D, U, R = "-hb", "-v", "-vb", "-h"
    di = view(ma_id, U, "70%", "disasm", follow="►")
    re = view(di.id, R, "50%", "regs")
    st = view(re.id, D, "35%", "stack")
    ba = view(ma_id, R, "25%", "backtrace", follow="►")

    mapping = {"regs": re, "legend": re, "disasm": di, "code": di, "stack": st, "backtrace": ba}
    for section, pane in mapping.items():
        contextoutput(section, pane.path, clearing=True, banner="none")
# fmt: on


def init():
    design()
    atexit.register(lambda: [call(f"tmux kill-pane -t {p.id}") for p in panes])
    atexit.register(lambda: [os.remove(f) for f in fifos if os.path.exists(f)])

    call("tmux set-option mouse on")
    call("tmux set-option pane-border-status top")
    call("tmux bind-key -T root M-z resize-pane -Z")
    for k, d in [("h", "L"), ("j", "D"), ("k", "U"), ("l", "R")]:
        call(f"tmux bind-key -T root M-{k} select-pane -{d}")
    for k, d in [("Left", "L"), ("Down", "D"), ("Up", "U"), ("Right", "R")]:
        call(f"tmux bind-key -T root M-{k} select-pane -{d}")
    atexit.register(lambda: call(f"tmux set-option pane-border-status off"))

    WrapCommand()


def _on_first_prompt():
    gdb.events.before_prompt.disconnect(_on_first_prompt)  # run once
    init()


if os.environ.get("TMUX") and os.environ.get("TMUX_PANE"):
    gdb.events.before_prompt.connect(_on_first_prompt)
