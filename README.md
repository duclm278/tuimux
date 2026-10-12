# tuimux

A customizable TUI+tmux setup for [pwndbg](https://github.com/pwndbg/pwndbg). It splits your tmux window into panes and streams each pwndbg context section into its own scrollable TUI viewer with wrapping, mouse, copy-on-select, search and follow support.

![Layout simple](assets/simple.png)

## Features

- **Automatic layout.** Panes are created on the first prompt, only when GDB runs inside tmux.
- **A real viewer in every pane.** Scroll in both directions, toggle wrap, select text with the mouse. Colors (SGR) are preserved.
- **Vim-like search.** `/` and `?` with incremental, regex matching, highlighted results and a `[i/n]` counter.
- **Follow mode.** On update, a pane re-centers the first line matching a regex (like pwndbg's `►`).
- **Clipboard integration.** Dragging copies via OSC 52 and into the tmux paste buffer.
- **Pane navigation.** Alt+h/j/k/l, Alt+Arrows and Alt+z work without the tmux prefix.
- **Clean teardown.** Panes, FIFOs and tmux options are restored when GDB exits.
- **Customizable.** A layout is just a Python function; example configs are included.

## Requirements

- [pwndbg](https://pwndbg.re/stable/setup) (`~/.gdbinit` setup or pwndbg-gdb installer), started inside tmux
- [tmux](https://github.com/tmux/tmux)

## Installation

```sh
git clone https://github.com/duclm278/tuimux
echo "source $PWD/tuimux/gdbinit.py" >> ~/.gdbinit
```

Make sure tuimux is sourced **after** pwndbg:

```gdb
# Only needed for ~/.gdbinit setup
source /path/to/pwndbg/gdbinit.py

set context-clear-screen on
set follow-fork-mode parent

source /path/to/tuimux/gdbinit.py

# Recommended, see "Follow"
# set context-disasm-lines 32
# set context-code-lines 32
set context-stack-lines 128
set context-backtrace-lines 128
```

Start `tmux`, then run `gdb ./binary` (or `pwngdb ./binary`). Panes appear at the first prompt.

> If pwndbg isn't importable, tuimux exits silently. If no layout appears, check that pwndbg is sourced first.

## Usage

### Viewer keys

Click or focus a pane, then:

| Key | Action |
| --- | --- |
| `h` `j` `k` `l` / Arrows | Scroll 10 columns or rows |
| `H` `J` `K` `L` / Shift+Arrows | Scroll 20 columns or rows |
| `PgUp` `PgDn` | Scroll one page |
| `0` / `Home`, `$` / `End` | Line start / end |
| `g` , `G` | Top / bottom |
| `/` , `?` | Search down / up (regex) |
| `n` , `N` | Next / previous match |
| `Esc` | Cancel the search prompt, or clear highlights |
| `w` | Toggle line wrap |
| `f` | Toggle follow (panes with follow only) |
| `m` | Toggle mouse capture (release it to use the terminal's native selection) |

**Mouse:** the wheel or a two-finger swipe scrolls vertically (hold Shift for horizontal). Dragging selects and copies on release.

### Search

- `/` or `?` opens a prompt at the bottom. Matches are found as you type; `Enter` accepts, `Esc` cancels. An empty `Enter` repeats the last search.
- Patterns are Python regexes. Matches are highlighted, the current one in orange, and the corner shows `[i/n]`.

### Follow

A pane created with `follow="REGEX"` re-centers a matching line on every update. The default layout follows `►` in `disasm` and `backtrace`, so the current instruction and frame stay in the middle as you step.

- `f` pauses or resumes follow. Follow overrides manual scrolling on the next update, so pause it to look around.
- **Hint:** if scrolling makes you lose focus on the current line, press `ff` to pause and resume, which re-centers it.
- Matching ignores colors. Examples: `►`, `^\s*►`, `(?i)error`.
- Standalone `fifo.py` also takes `--follow-mode`: `buffer` (default, first match in the whole unwrapped text, may span lines), `line` (first matching line, faster) or `last` (last matching line, fastest).

**Tip:** set `context-<section>-lines` high. The viewer scrolls, so you can browse freely around the current instruction, stack or call chain while follow keeps `►` centered.

### Status display

- **Title:** `disasm 64%,100%` shows how much of the content's bottom and right edge is visible (100% = fully visible). Hidden when everything fits.
- **Corner (bottom right):** active flags `f` (follow), `m` (mouse capture), `w` (wrap), shown for about 2 seconds after start or a toggle (`-` means none), plus the search position `[i/n]` while highlights are on.

### tmux bindings

Root-table bindings (no prefix needed):

| Key | Action |
| --- | --- |
| `Alt+h/j/k/l` / `Alt+Arrows` | Move between panes |
| `Alt+z` | Zoom or unzoom the pane |

tuimux also turns on `mouse` and `pane-border-status top`, and switches the latter off on exit.

### GDB command

`wrap` toggles wrapping in all viewer panes at once.

## Layouts

The layout lives in `design()`. Redefine it in your `~/.gdbinit` **after** sourcing `tuimux/gdbinit.py` to replace the default.

| File | Description |
| --- | --- |
| `simple.gdbinit` | Default layout |
| `simple-code.gdbinit` | Default plus a separate `code` pane |
| `splitmind.gdbinit` | [splitmind](https://github.com/jerdna-regeiz/splitmind)-style layout with input/output and ipython panes |
| `splitmind-code.gdbinit` | Splitmind layout plus a `code` pane |

Default (`simple`):

```txt
┌───────────────────────┬───────────────────────┐
│ disasm                │ regs                  │
│                       ├───────────────────────┤
│                       │ stack                 │
├───────────────────────┴───────────┬───────────┤
│ main (gdb)                        │ backtrace │
└───────────────────────────────────┴───────────┘
```

`simple-code`:

```txt
┌───────────────────────┬───────────────────────┐
│ disasm                │ regs                  │
├───────────────────────┤                       │
│ code                  ├───────────────────────┤
│                       │ stack                 │
├───────────────────────┴───────────┬───────────┤
│ main (gdb)                        │ backtrace │
└───────────────────────────────────┴───────────┘
```

`splitmind`:

```txt
┌───────────┬───────────────────────┬───────────┐
│ disasm    │ regs                  │ backtrace │
│           ├───────────────────────┤           │
│           │ stack                 │           │
│           ├───────────────────────┤           │
│           │ input/output          ├───────────┤
├───────────┴───────────────────────┤ ipython   │
│ main (gdb)                        │           │
└───────────────────────────────────┴───────────┘
```

![Layout splitmind](assets/splitmind.png)

`splitmind-code`:

```txt
┌───────────┬───────────────────────┬───────────┐
│ disasm    │ regs                  │ backtrace │
│           │                       │           │
├───────────┤───────────────────────┤           │
│ code      │ stack                 │           │
│           ├───────────────────────┤           │
│           │ input/output          ├───────────┤
├───────────┴───────────────────────┤ ipython   │
│ main (gdb)                        │           │
└───────────────────────────────────┴───────────┘
```

![Layout splitmind-code](assets/splitmind-code.png)

The `code` layouts give source its own pane (also with follow) and work best with the high `context-*-lines` values above. The input/output pane prints its tty; in GDB run `tty /dev/pts/<num>` to redirect the binary's I/O there.

### Writing your own

`design()` has two helpers:

```python
view(target, direction, size, title, wrap=True, mouse=True, follow=None)  # viewer pane fed by a FIFO
tmux(target, direction, size, title, cmd="cat -")                         # any command in a plain pane
```

Both split pane `target` and return a pane with `.id` and `.path`. Directions: `-hb` (left), `-v` (down), `-vb` (up), `-h` (right). `size` is a tmux `-l` value like `"30%"` or `"25"`. `follow` is a regex to keep centered. Then map pwndbg sections to panes:

```python
def design():
    ma_id = os.environ["TMUX_PANE"]
    call(f'tmux select-pane -T "main" -t {ma_id}')

    L, D, U, R = "-hb", "-v", "-vb", "-h"
    di = view(ma_id, U, "60%", "disasm", follow="►")
    co = view(di.id, D, "55%", "code", follow="►")
    st = view(di.id, R, "40%", "stack")

    for section, pane in {"disasm": di, "code": co, "stack": st}.items():
        contextoutput(section, pane.path, clearing=True, banner="none")
```

This puts disasm above the main pane, code below disasm, and stack to the right of disasm. Sections can share a pane (as `regs`/`legend` and `disasm`/`code` do in the default layout). Use `tmux()` for panes that don't need the viewer (see `splitmind.gdbinit`).

See [pwndbg's docs](https://pwndbg.re/stable/configuration/config/#context-sections) for available sections. Unmapped sections stay in the main GDB pane.

## How it works

1. `gdbinit.py` waits for the first GDB prompt, then runs `init()`. You can override `design()` and `init()`.
2. Each viewer pane gets a FIFO at `/tmp/view-<pid>-<title>.fifo` and runs `fifo.py <fifo> <title>`.
3. pwndbg's `contextoutput` redirects each section into its FIFO.
4. `fifo.py` treats a clear-screen sequence as a new frame and redraws the pane, handling escape sequences, colors, wide characters, selection, scrolling, search and follow.
5. On exit, `atexit` handlers kill the panes, delete the FIFOs and restore tmux settings.

`fifo.py` also works standalone:

```sh
mkfifo /tmp/demo.fifo
python3 fifo.py /tmp/demo.fifo demo --follow '►' --wrap --mouse

# in another terminal
echo hello > /tmp/demo.fifo
```

Options: `-f/--follow REGEX`, `--follow-mode {buffer,line,last}`, `-m/--mouse`, `-w/--wrap` (all off by default).

## Files

```
gdbinit.py              GDB entry point: creates panes, wires up pwndbg
fifo.py                 Standalone FIFO viewer used in each pane
simple.gdbinit          Default layout
simple-code.gdbinit     Default layout with code pane
splitmind.gdbinit       Splitmind-style layout
splitmind-code.gdbinit  Splitmind-style layout with code pane
```

## Troubleshooting

- **Nothing happens.** GDB must run inside tmux (`$TMUX` and `$TMUX_PANE` set), with pwndbg loaded first.
- **Can't select text with the terminal's native selection.** Press `m` in the pane.
- **Can't scroll, the view keeps jumping back.** Follow is on. Press `f` to pause it.
- **Lost the current line after scrolling.** If `f` is still on, press `ff` to re-center it.
- **Stale FIFOs after a crash.** `rm /tmp/view-*.fifo`
- **warning: GDB: Failed to set controlling terminal: Operation not permitted.** Ignore it; [splitmind](https://github.com/jerdna-regeiz/splitmind) recommends the same.

## Acknowledgements

- [kittydbg](https://github.com/k4lizen/kittydbg): gdbinit config using kitty tiling to use the empty space in pwndbg output.
- [pwnmux](https://github.com/joaogodinho/pwnmux): pwndbg and tmux config with multiple panes for different contexts.
- [splitmind](https://github.com/jerdna-regeiz/splitmind): Python module to set up a layout of splits for presented information.
