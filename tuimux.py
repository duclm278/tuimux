import atexit
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import run

import gdb
from pwndbg.commands.context import contextoutput, resetcontextoutput

__all__ = ["L", "D", "U", "R", "make", "view", "layout", "init_gdb"]

L, D, U, R = "L", "D", "U", "R"
VIEW = f"python3 {Path(__file__).resolve().parent / 'viewer.py'}"
DEFAULT_OPTS = dict(clearing=True, banner="none")
LAYOUT_FLAGS = {"-y": True, "--yes": True, "-n": False, "--no": False}
NO_INIT_MUX = "--no-init-mux"
LAYOUT_USAGE = "Usage: tuimux layout NAME [-y|--yes|-n|--no] [--no-init-mux]"


def sh(cmd):
    p = run(cmd, capture_output=True, shell=True, text=True)
    if p.returncode:
        raise RuntimeError(f"tuimux: {cmd}: {p.stderr.strip()}")
    return p.stdout.strip()


@dataclass
class Area:
    id: str
    path: str = None
    sections: tuple = ()
    section_options: dict = field(default_factory=dict)

    def add(self, *sections, **options):
        self.sections += sections
        self.section_options.update(
            dict.fromkeys(sections, {**DEFAULT_OPTS, **options})
        )
        return self


class Backend:
    sides, commands = {}, {}
    parse_size = parse_open = lambda x: x

    @classmethod
    def call(cls, action, **kw):
        cmd = cls.commands.get(action)
        return sh(cmd.format(**kw)) if cmd else ""

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
        "exit": "tmux kill-pane -t {base.id}",
        "open": 'tmux split-window -t {base.id} {side} -l {size} -P -F "#{{pane_id}}:#{{pane_tty}}" -d "{cmd}"',
    }
    parse_open = lambda x: x.split(":", 1)

    @classmethod
    def init(cls):
        sh("tmux set -w mouse on")
        sh("tmux set -w pane-border-status top")
        sh("tmux bind-key -T root M-z resize-pane -Z")
        keys = ["h", "j", "k", "l", "Left", "Down", "Up", "Right"]
        for key, direction in zip(keys, "LDUR" * 2):
            sh(f"tmux bind-key -T root M-{key} select-pane -{direction}")


MUXES = [Tmux]
MUX, LAYOUT = None, None
SESSION_DIR = None
areas, layouts = [], {}


def make(base, side, size, name, cmd="cat -"):
    areas.append(MUX.open(base, side, size, name, cmd=cmd))
    return areas[-1]


def view(
    base,
    side,
    size,
    name,
    follow=None,
    follow_mode=None,
    follow_live=True,
    mouse=True,
    wrap=False,
    **options,
):
    path = SESSION_DIR / f"{name}.tty"
    args = [VIEW, str(path), name, "--tty", "--sync", str(SESSION_DIR)]
    if follow:
        args += ["--follow", follow]
    if follow_mode:
        args += ["--follow-mode", follow_mode]
    if follow_live:
        args.append("--follow-live")
    if mouse:
        args.append("--mouse")
    if wrap:
        args.append("--wrap")

    area = make(base, side, size, name, cmd=" ".join(args))
    area.path = str(path)
    area.add(name, **options)
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
    "context-stack-lines": 8,
    "context-disasm-lines": 10,
})
def duo_1(ma):
    sections = gdb.parameter("context-sections").split()
    area = view(ma, R, "50%", "legend")
    for section in sections:
        area.add(section, banner="both")

@layout(opts={
    "context-code-lines": 14,
    "context-stack-lines": 8,
    "context-disasm-lines": 10,
})
def duo_2(ma):
    sections = gdb.parameter("context-sections").split()
    area = view(ma, L, "50%", "legend")
    for section in sections:
        area.add(section, banner="both")

@layout(opts={
    "context-code-lines": 14,
    "context-stack-lines": 1024,
    "context-disasm-lines": 10,
})
def simple(ma):
    di = view(ma, U, "70%", "disasm").add("code")
    re = view(di, R, "50%", "regs").add("legend")
    st = view(re, D, "35%", "stack")
    ba = view(ma, R, "25%", "backtrace", follow="►")
    ba.add("threads", banner="top").add("expressions", banner="top")

@layout(opts={
    "context-code-lines": 1024,
    "context-stack-lines": 1024,
    "context-disasm-lines": 1024,
})
def simple_code(ma):
    di = view(ma, U, "70%", "disasm", follow="►")
    re = view(di, R, "50%", "regs").add("legend")
    st = view(re, D, "35%", "stack")
    ba = view(ma, R, "25%", "backtrace", follow="►")
    co = view(di, D, "55%", "code", follow="►")
    ba.add("threads", banner="top").add("expressions", banner="top")

@layout(opts={
    "context-code-lines": 18,
    "context-stack-lines": 1024,
    "context-disasm-lines": 10,
})
def splitmind(ma):
    ba = view(ma, R, "25%", "backtrace", follow="►")
    di = view(ma, U, "80%", "disasm").add("code")
    io = make(di, R, "55%", "input/output", cmd="tty; tail -f /dev/null")
    st = view(io, U, "75%", "stack")
    re = view(st, U, "60%", "regs").add("legend")
    ip = make(ba, D, "30%", "ipython", cmd="ipython3")
    ba.add("threads", banner="top").add("expressions", banner="top")

@layout(opts={
    "context-code-lines": 1024,
    "context-stack-lines": 1024,
    "context-disasm-lines": 1024,
})
def splitmind_code(ma):
    ba = view(ma, R, "25%", "backtrace", follow="►")
    di = view(ma, U, "80%", "disasm", follow="►")
    io = make(di, R, "55%", "input/output", cmd="tty; tail -f /dev/null")
    st = view(io, U, "75%", "stack")
    re = view(st, U, "60%", "regs").add("legend")
    ip = make(ba, D, "30%", "ipython", cmd="ipython3")
    co = view(di, D, "55%", "code", follow="►")
    ba.add("threads", banner="top").add("expressions", banner="top")
# fmt: on


def new_session_dir():
    global SESSION_DIR
    SESSION_DIR = Path(tempfile.mkdtemp(prefix="tuimux-"))


def wait_for_view(area):
    for _ in range(500):  # the viewer creates its PTY a moment after the area opens
        if Path(area.path).exists():
            return
        time.sleep(0.01)
    raise RuntimeError(f"tuimux: {area.id} did not start")


def remove_view(area):
    for section in area.sections:
        resetcontextoutput(section)
    area.sections = ()


def cleanup():
    global LAYOUT, SESSION_DIR
    for area in areas:
        remove_view(area)
    for area in reversed(areas):
        with suppress(RuntimeError):
            MUX.exit(area)
    areas.clear()
    if SESSION_DIR:
        with suppress(OSError):
            for path in SESSION_DIR.iterdir():
                with suppress(OSError):
                    path.unlink()
            SESSION_DIR.rmdir()
    SESSION_DIR = LAYOUT = None


def use(name, init_mux=True):
    global MUX, LAYOUT
    if name not in layouts:
        raise gdb.GdbError(f"Unknown layout: {name}. Available: {', '.join(layouts)}")
    mux = next((m for m in MUXES if m.live()), None)
    if not mux:
        return False

    cleanup()
    if not MUX:
        if init_mux:
            mux.init()
        atexit.register(cleanup)
    MUX = mux

    new_session_dir()
    main = MUX.main()
    MUX.name(main, "main")
    try:
        layouts[name](main)
        for area in areas:
            if area.sections:
                wait_for_view(area)
            for section in area.sections:
                section_options = area.section_options.get(section, {})
                contextoutput(section, area.path, **section_options)
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


def switch_layout(arg, from_tty):
    tokens = arg.split()
    if not tokens:
        print(f"Current layout: {LAYOUT or 'none'}")
        print(f"Available layouts: {', '.join(layouts)}")
        return

    choices = {LAYOUT_FLAGS[t] for t in tokens if t in LAYOUT_FLAGS}
    names = [t for t in tokens if t not in LAYOUT_FLAGS and t != NO_INIT_MUX]
    if len(choices) > 1 or len(names) != 1 or names[0].startswith("-"):
        raise gdb.GdbError(LAYOUT_USAGE)
    name = names[0]
    apply = next(iter(choices), None)

    if not use(name, init_mux=NO_INIT_MUX not in tokens):
        raise gdb.GdbError("tuimux layout requires GDB to run inside mux")

    opts = layouts[name].suggested_options
    if opts and apply is None and from_tty:
        print(
            "Suggested options:",
            *(f"  set {k} {v}" for k, v in opts.items()),
            sep="\n",
        )
        try:
            answer = input("Apply? [Y/n] ").strip().lower()
        except EOFError:
            answer = ""
        apply = answer in ("", "y", "yes")
    if apply:
        for k, v in opts.items():
            gdb.execute(f"set {k} {v}", from_tty=False)
    gdb.execute("context", from_tty=False)


def complete_layout(text, word):
    text = text.removeprefix("tuimux layout").lstrip()
    args = text.split()
    prefix = "" if not text or text[-1].isspace() else args.pop()
    flags = (*LAYOUT_FLAGS, NO_INIT_MUX)
    names = [arg for arg in args if arg not in flags]
    if len(names) > 1 or any(name.startswith("-") for name in names):
        return []
    candidates = [] if names else list(layouts)
    if not any(arg in LAYOUT_FLAGS for arg in args):
        candidates.extend(LAYOUT_FLAGS)
    if NO_INIT_MUX not in args:
        candidates.append(NO_INIT_MUX)
    return [candidate for candidate in candidates if candidate.startswith(prefix)]


def init_gdb():
    add_command("tuimux", lambda *_: None, prefix=True)
    add_command(
        "tuimux layout",
        switch_layout,
        help="List or switch layouts.",
        complete=complete_layout,
    )
    gdb.execute("alias mux = tuimux")
