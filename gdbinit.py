import atexit
import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import run

import gdb

try:
    from pwndbg.commands.context import contextoutput, resetcontextoutput
except ImportError:
    exit()


L, D, U, R = "L", "D", "U", "R"
VIEW = f"python3 {Path(__file__).resolve().parent / 'fifo.py'}"
DEFAULT_OPTS = dict(clearing=True, banner="none")
TOGGLES = {"mouse": "m", "wrap": "w"}
LAYOUT_FLAGS = {"-y": True, "--yes": True, "-n": False, "--no": False}
LAYOUT_USAGE = "Usage: tuimux layout NAME [-y|--yes|-n|--no]"


def exec(cmd):
    p = run(cmd, capture_output=True, shell=True, text=True)
    if p.returncode:
        raise RuntimeError(f"tuimux: {cmd}: {p.stderr.strip()}")
    return p.stdout.strip()


@dataclass
class Area:
    id: str
    path: str = None
    sections: tuple = ()
    options: dict = field(default_factory=dict)

    def add(self, *sections):
        self.sections += sections
        return self


class Backend:
    sides, commands = {}, {}
    parse_size = parse_open = lambda x: x

    @classmethod
    def call(cls, action, **kw):
        cmd = cls.commands.get(action)
        return exec(cmd.format(**kw)) if cmd else ""

    @classmethod
    def live(cls):
        return cls.call("live")

    @classmethod
    def main(cls):
        return Area(cls.call("main"))

    @classmethod
    def name(cls, base, name):
        cls.call("name", base=base, name=name)

    @classmethod
    def send(cls, base, keys):
        cls.call("send", base=base, keys=keys)

    @classmethod
    def exit(cls, base):
        cls.call("exit", base=base)

    @classmethod
    def open(cls, base, side, size, name, cmd="cat -"):
        out = cls.call(
            "open",
            base=base,
            side=cls.sides[side],
            size=cls.parse_size(size),
            name=name,
            cmd=cmd,
        )
        area = Area(*cls.parse_open(out))
        cls.name(area, name)
        return area

    @classmethod
    def init(cls):
        pass


class Tmux(Backend):
    sides = {L: "-hb", D: "-v", U: "-vb", R: "-h"}
    commands = {
        "live": "echo $TMUX",
        "main": "echo $TMUX_PANE",
        "name": "tmux select-pane -t {base.id} -T {name}",
        "send": "tmux send-keys -t {base.id} {keys}",
        "exit": "tmux kill-pane -t {base.id}",
        "open": 'tmux split-window -t {base.id} {side} -l {size} -P -F "#{{pane_id}}:#{{pane_tty}}" -d "{cmd}"',
    }
    parse_open = lambda x: x.split(":", 1)

    @classmethod
    def init(cls):
        exec("tmux set -w mouse on")
        exec("tmux set -w pane-border-status top")
        exec("tmux bind-key -T root M-z resize-pane -Z")
        keys = ["h", "j", "k", "l", "Left", "Down", "Up", "Right"]
        for key, direction in zip(keys, "LDUR" * 2):
            exec(f"tmux bind-key -T root M-{key} select-pane -{direction}")


MUXES = [Tmux]
MUX, LAYOUT = None, None
areas, fifos, layouts = [], [], {}


def open(base, side, size, name, cmd="cat -"):
    areas.append(MUX.open(base, side, size, name, cmd=cmd))
    return areas[-1]


def view(
    base,
    side,
    size,
    name,
    follow=None,
    follow_mode=None,
    mouse=True,
    wrap=False,
    **options,
):
    fifo = f"/tmp/view-{os.getpid()}-{name}.fifo"
    os.mkfifo(fifo)
    fifos.append(fifo)

    args = [VIEW, fifo, name]
    if wrap:
        args.append("--wrap")
    if mouse:
        args.append("--mouse")
    if follow:
        args += ["--follow", follow]
    if follow_mode:
        args += ["--follow-mode", follow_mode]

    area = open(base, side, size, name, cmd=" ".join(args))
    area.path, area.sections = fifo, (name,)
    area.options = {**DEFAULT_OPTS, **options}
    return area


def layout(opts=None):
    def decorator(fn):
        layouts[fn.__name__] = fn
        fn.suggested_options = opts or {}
        return fn

    return decorator


# fmt: off
@layout(opts={
    "context-code-lines": 14,
    "context-stack-lines": 1024,
    "context-disasm-lines": 10,
})
def simple(ma):
    di = view(ma, U, "70%", "disasm").add("code")
    rg = view(di, R, "50%", "regs").add("legend")
    st = view(rg, D, "35%", "stack")
    ba = view(ma, R, "25%", "backtrace", follow="►")

@layout(opts={
    "context-code-lines": 1024,
    "context-stack-lines": 1024,
    "context-disasm-lines": 1024,
})
def simple_code(ma):
    di = view(ma, U, "70%", "disasm", follow="►")
    rg = view(di, R, "50%", "regs").add("legend")
    st = view(rg, D, "35%", "stack")
    ba = view(ma, R, "25%", "backtrace", follow="►")
    co = view(di, D, "55%", "code", follow="►")

@layout(opts={
    "context-code-lines": 18,
    "context-stack-lines": 1024,
    "context-disasm-lines": 10,
})
def splitmind(ma):
    ba = view(ma, R, "25%", "backtrace", follow="►")
    di = view(ma, U, "80%", "disasm").add("code")
    io = open(di, R, "55%", "input/output", cmd="tty; tail -f /dev/null")
    st = view(io, U, "75%", "stack")
    rg = view(st, U, "60%", "regs").add("legend")
    ip = open(ba, D, "30%", "ipython", cmd="ipython3")

@layout(opts={
    "context-code-lines": 1024,
    "context-stack-lines": 1024,
    "context-disasm-lines": 1024,
})
def splitmind_code(ma):
    ba = view(ma, R, "25%", "backtrace", follow="►")
    di = view(ma, U, "80%", "disasm", follow="►")
    io = open(di, R, "55%", "input/output", cmd="tty; tail -f /dev/null")
    st = view(io, U, "75%", "stack")
    rg = view(st, U, "60%", "regs").add("legend")
    ip = open(ba, D, "30%", "ipython", cmd="ipython3")
    co = view(di, D, "55%", "code", follow="►")
# fmt: on


def cleanup():
    global LAYOUT
    for section in {s for a in areas for s in a.sections}:
        resetcontextoutput(section)
    for area in reversed(areas):
        with suppress(RuntimeError):
            MUX.exit(area)
    for fifo in fifos:
        with suppress(FileNotFoundError):
            os.remove(fifo)
    areas.clear()
    fifos.clear()
    LAYOUT = None


def use(name, init_mux=True):
    global MUX, LAYOUT
    if name not in layouts:
        raise gdb.GdbError(f"Unknown layout: {name}. Available: {', '.join(layouts)}")
    mux = next((m for m in MUXES if m.live()), None)
    if not mux:
        return False
    if LAYOUT == name:
        return True

    cleanup()
    if not MUX:
        if init_mux:
            mux.init()
        atexit.register(cleanup)
    MUX = mux

    main = MUX.main()
    MUX.name(main, "main")
    try:
        layouts[name](main)
        for area in areas:
            for section in area.sections:
                contextoutput(section, area.path, **area.options)
    except Exception:
        cleanup()
        raise
    LAYOUT = name
    return True


def add_command(name, fn, prefix=False, help=None, complete=None):
    ns = {"__doc__": help, "invoke": lambda self, arg, tty: fn(arg, tty)}
    if complete:
        ns["complete"] = lambda self, text, word: complete(text, word)
    type("C", (gdb.Command,), ns)(name, gdb.COMMAND_USER, prefix=prefix)


def completer(choices):
    return lambda text, word: [
        c for c in choices() if c.startswith(text if word is None else word)
    ]


def toggle(arg, from_tty):
    key = TOGGLES.get(arg.strip())
    if not key:
        raise gdb.GdbError(f"Usage: tuimux toggle {'|'.join(TOGGLES)}")
    for area in areas:
        if area.path in fifos:
            MUX.send(area, key)


def switch_layout(arg, from_tty):
    tokens = arg.split()
    if not tokens:
        print(f"Current layout: {LAYOUT or 'none'}")
        print(f"Available layouts: {', '.join(layouts)}")
        return

    choices = {LAYOUT_FLAGS[t] for t in tokens if t in LAYOUT_FLAGS}
    names = [t for t in tokens if t not in LAYOUT_FLAGS]
    if len(choices) > 1 or len(names) != 1 or names[0].startswith("-"):
        raise gdb.GdbError(LAYOUT_USAGE)
    name = names[0]
    apply = next(iter(choices), None)

    if not use(name):
        raise gdb.GdbError("tuimux layout requires GDB to run inside mux")

    opts = layouts[name].suggested_options
    if opts and apply is None and from_tty:
        print(
            "Suggested options:",
            *(f"  set {k} {v}" for k, v in opts.items()),
            sep="\n",
        )
        try:
            answer = input("Apply these options? [Y/n] ").strip().lower()
        except EOFError:
            answer = ""
        apply = answer in ("", "y", "yes")
    if apply:
        for k, v in opts.items():
            gdb.execute(f"set {k} {v}", from_tty=False)
    gdb.execute("context", from_tty=False)


def layout_complete(text, word):
    text = text.removeprefix("tuimux layout").lstrip()
    args = text.split()
    prefix = "" if not text or text[-1].isspace() else args.pop()
    if any(a not in LAYOUT_FLAGS for a in args):
        return []
    return [name for name in layouts if name.startswith(prefix)]


add_command("tuimux", lambda *_: None, prefix=True)
add_command(
    "tuimux toggle",
    toggle,
    help="Toggle options in all viewers.",
    complete=completer(lambda: TOGGLES),
)
add_command(
    "tuimux layout",
    switch_layout,
    help="List or switch layouts.",
    complete=layout_complete,
)
