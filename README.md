# tuimux

A customizable TUI+tmux setup for [pwndbg](https://github.com/pwndbg/pwndbg). It splits your tmux window into panes and streams each pwndbg context section into its own scrollable TUI viewer.

![Layout simple](assets/simple.png)

## Features

- **Switchable layouts.** `tuimux layout` builds panes at any prompt and tears down the old ones.
- **Real viewers.** Scroll, wrap, mouse select, SGR colors preserved.
- **History.** Every redraw is kept as a frame, so you can step back through earlier states and compare them.
- **Synced panes.** History moves and the follow/mouse/wrap toggles apply to all viewers at once.
- **Real TTYs.** Each viewer exposes a PTY, so pwndbg sees the pane's real size and lays out for it.
- **Search.** `/` and `?` with incremental regex matching and a `[i/n]` counter.
- **Follow mode.** Panes re-center the line matching a regex (like pwndbg's `►`) on every update.
- **Clipboard.** Dragging copies via OSC 52 and the tmux paste buffer.
- **Pane navigation.** Alt+h/j/k/l, Alt+Arrows and Alt+z, no prefix needed.
- **Customizable.** A layout is a short Python function.

## Requirements

- [pwndbg](https://pwndbg.re/stable/setup) (~/.gdbinit setup or pwndbg-gdb installer setup), started inside tmux
- [tmux](https://github.com/tmux/tmux)

## Installation

```sh
git clone https://github.com/duclm278/tuimux
```

Add this to your `~/.gdbinit`, with tuimux sourced **after** pwndbg:

```gdb
# Only needed for ~/.gdbinit setup
source /path/to/pwndbg/gdbinit.py

set context-clear-screen on
set follow-fork-mode parent

source /path/to/tuimux/gdbinit.py
tuimux layout simple -y
```

Note that `set context-clear-screen on` is recommended: a clear-screen sequence is what marks the start of a new frame in each viewer (and so each history entry).

Start `tmux`, then run `gdb ./binary` (or `pwndbg ./binary` depending on your setup). Panes appear when `tuimux layout` runs.

> If pwndbg isn't importable, tuimux exits silently.

## Usage

### pwntools

Open tmux first, then set the terminal in your pwntools script:

```python
context.terminal = ["tmux", "new-window"]
```

### Viewer keys

Click or focus a pane, then:

| Key | Action |
| --- | --- |
| `h` `j` `k` `l` / Arrows | Scroll 10 columns or rows |
| `H` `J` `K` `L` / Shift+Arrows | Scroll 20 columns or rows |
| `PgUp` `PgDn` | Scroll one page |
| `0` / `Home`, `$` / `End` | Line start / end |
| `g` , `G` | Top / bottom |
| `[` , `]` | Older / newer history frame |
| `{` , `}` | Oldest history frame / back to live |
| `/` , `?` | Search down / up (regex) |
| `n` , `N` | Next / previous match |
| `Esc` | Cancel the search prompt, or clear highlights |
| `w` | Toggle line wrap (off by default) |
| `f` | Toggle follow (panes with follow only) |
| `m` | Toggle mouse capture (release it to use the terminal's native selection) |

**Mouse:** the wheel or a two-finger swipe scrolls vertically (hold Shift for horizontal). Dragging selects and copies on release.

### History

Each time pwndbg redraws a section (a clear-screen sequence), the viewer seals the previous output as a frame. Frames stay available while you keep debugging.

- `[` / `]` step to the older / newer frame, `{` jumps to the oldest, `}` returns to the live frame.
- While browsing, the corner shows your position, e.g. `3/47` (frame 3 of 47). The live frame is always the last one.
- The live frame keeps receiving output in the background, so nothing is lost while you look at the past.
- Follow, search and selection all work on the frame being shown. Search matches are cleared when you switch frames.
- With `follow_live` (on by default), a new frame arriving pulls the pane back to live. Pass `follow_live=False` to `view()` to stay on the frame you are inspecting.
- History moves are [synced](#sync) across all panes, so `[` in `regs` also steps `disasm`, `stack`, etc. to the same point in time.

### Sync

Viewers in one layout share a control socket, so these actions are broadcast to every sibling pane:

- History moves: `[` `]` `{` `}`
- Toggles: `f` (follow), `m` (mouse capture), `w` (wrap)

Follow only applies to panes created with `follow=...`. Scrolling and search stay local to the pane.

### Search

- `/` or `?` opens a prompt at the bottom. Matches are found as you type; `Enter` accepts, `Esc` cancels. An empty `Enter` repeats the last search.
- Patterns are Python regexes. Matches are highlighted, the current one in orange, and the corner shows `[i/n]`.

### Follow

A pane created with `follow="REGEX"` re-centers a matching line on every update. Any pane can follow any regex. For example, the built-in layouts follow `►` in `backtrace` (and in `disasm` and `code` for the `*_code` layouts), so the current frame, instruction and source line stay in the middle as you step.

- `f` pauses or resumes follow. Follow overrides manual scrolling on the next update, so pause it to look around.
- **Tip:** if scrolling makes you lose focus on the current line, press `ff` to pause and resume, which re-centers it.
- Matching ignores colors. Examples: `►`, `^\s*►`, `(?i)error`.
- `view()` also takes `follow_mode`, and standalone `viewer.py` takes `--follow-mode`: `buffer` (default, first match in the whole unwrapped text, may span lines), `line` (first matching line, faster) or `last` (last matching line, fastest).

**Tip:** set `context-<section>-lines` high. The viewer scrolls, so you can browse freely while follow keeps `►` centered. The provided `*_code` layouts will also suggest this for you.

### Status

- **Title:** `disasm 64%,100%` shows how much of the content's bottom and right edge is visible (100% = fully visible). Hidden when everything fits.
- **Corner (bottom right):** active flags `f` (follow), `m` (mouse capture), `w` (wrap), shown briefly after start or a toggle (`-` means none), plus the search position `[i/n]` while highlights are on, and the history position `i/n` while browsing past frames.

### tmux bindings

Root-table bindings (no prefix needed):

| Key | Action |
| --- | --- |
| `Alt+h/j/k/l` / `Alt+Arrows` | Move between panes |
| `Alt+z` | Zoom or unzoom the pane |

On the first layout, tuimux also turns on `mouse` and `pane-border-status top` for the window and installs these bindings. Pane titles are set to the section names (`main` for the GDB pane). Use `--no-init-mux` to skip this setup.

### GDB commands

| Command | Description |
| --- | --- |
| `tuimux layout` | Show the current and available layouts |
| `tuimux layout NAME [-y\|--yes\|-n\|--no] [--no-init-mux]` | Switch to layout `NAME` |

`mux` is an alias for `tuimux`. Commands support tab completion.

#### Layout command

Switching a layout creates its panes, redirects the pwndbg sections into them and then redraws with `context`.

If the layout has suggested options (for example `context-disasm-lines`), they are handled like this:

- `-y` / `--yes`: apply them silently.
- `-n` / `--no`: skip them.
- Neither flag: list them and ask `Apply? [Y/n]` (interactive use only).

`--no-init-mux` skips the one-time tmux setup (mouse, pane borders, key bindings), for when you manage those yourself.

Suggested options per layout:

| Layout | `context-code-lines` | `context-stack-lines` | `context-disasm-lines` |
| --- | --- | --- | --- |
| `duo_1` | 14 | 8 | 10 |
| `duo_2` | 14 | 8 | 10 |
| `simple` | 14 | 1024 | 10 |
| `simple_code` | 1024 | 1024 | 1024 |
| `splitmind` | 18 | 1024 | 10 |
| `splitmind_code` | 1024 | 1024 | 1024 |

## Layouts

| Layout | Example | Description |
| --- | --- | --- |
| `duo_1` | | GDB on the left, one viewer on the right showing all context sections |
| `duo_2` | | Mirror of `duo_1`: viewer on the left, GDB on the right |
| `simple` | `simple.gdbinit` | Default |
| `simple_code` | `simple_code.gdbinit` | Default plus a separate `code` pane |
| `splitmind` | | [splitmind](https://github.com/jerdna-regeiz/splitmind)-style, with input/output and ipython panes |
| `splitmind_code` | | Splitmind plus a `code` pane |

`duo_1` / `duo_2`:

```txt
duo_1                        duo_2
┌────────────┬────────────┐  ┌────────────┬────────────┐
│ main (gdb) │ regs       │  │ regs       │ main (gdb) │
│            │ disasm     │  │ disasm     │            │
│            │ code       │  │ code       │            │
│            │ stack      │  │ stack      │            │
│            │ ...        │  │ ...        │            │
└────────────┴────────────┘  └────────────┴────────────┘
```

Both put every section from `context-sections` into a single scrollable viewer, each with its own banner, which suits a narrow terminal or monitor. Sections are read when the layout is created, so change `context-sections` first if you want a different set.

`simple`:

```txt
┌───────────────────────┬───────────────────────┐
│ disasm                │ regs                  │
│ ...                   ├───────────────────────┤
│                       │ stack                 │
├───────────────────────┴───────────┬───────────┤
│ main (gdb)                        │ backtrace │
│                                   │ ...       │
└───────────────────────────────────┴───────────┘
```

`simple_code`:

```txt
┌───────────────────────┬───────────────────────┐
│ disasm                │ regs                  │
│                       │                       │
├───────────────────────├───────────────────────┤
│ code                  │ stack                 │
│                       │                       │
├───────────────────────┴───────────┬───────────┤
│ main (gdb)                        │ backtrace │
│                                   │ ...       │
└───────────────────────────────────┴───────────┘
```

`splitmind`:

```txt
┌───────────┬───────────────────────┬───────────┐
│ disasm    │ regs                  │ backtrace │
│ ...       ├───────────────────────┤ ...       │
│           │ stack                 │           │
│           ├───────────────────────┤           │
│           │ input/output          ├───────────┤
├───────────┴───────────────────────┤ ipython   │
│ main (gdb)                        │           │
└───────────────────────────────────┴───────────┘
```

![Layout splitmind](assets/splitmind.png)

`splitmind_code`:

```txt
┌───────────┬───────────────────────┬───────────┐
│ disasm    │ regs                  │ backtrace │
│           ├───────────────────────┤ ...       │
├───────────┤ stack                 │           │
│ code      ├───────────────────────┤           │
│           │ input/output          ├───────────┤
├───────────┴───────────────────────┤ ipython   │
│ main (gdb)                        │           │
└───────────────────────────────────┴───────────┘
```

![Layout splitmind_code](assets/splitmind_code.png)

In the splitmind layouts, the input/output pane prints its tty; in GDB run `tty /dev/pts/<num>` to redirect the binary's I/O there.

### Writing your own

A layout receives the main (GDB) pane and splits panes off it. Import the helpers from `tuimux` inside a `python` block:

```python
from tuimux import *
L, D, U, R          # sides: left, down, up, right
@layout(opts=None)  # decorator; opts = suggested GDB options
make(base, side, size, name, cmd="cat -")  # area without viewer's features
view(base, side, size, name, follow=None, follow_mode=None, follow_live=True, mouse=True, wrap=False, **options)
```

`size` is a tmux `-l` value like `"30%"`. Extra `**options` go to pwndbg's `contextoutput`. Call `.add("section")` on a view to show more sections in it; unmapped sections stay in the GDB pane (see [pwndbg's sections](https://pwndbg.re/stable/configuration/config/#context-sections)).

`follow_live` controls whether a new frame pulls the viewer out of [history](#history) back to live.

Example (`custom.gdbinit`):

```python
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
    io = make(di, R, "55%", "input/output", cmd="tty; tail -f /dev/null")
    st = view(io, U, "75%", "stack")
    re = view(st, U, "60%", "regs").add("legend")
    ip = make(ba, D, "30%", "ipython", cmd="ipython3")
    co = view(di, D, "55%", "code", follow="►")
    ba.add("threads", banner="top")
end
tuimux layout custom -y
```

Each pane is split relative to its base, so order matters.

## How it works

1. `tuimux layout <name>` finds a live multiplexer (tmux), tears down any previous layout, creates a session directory `/tmp/tuimux-?` and runs the layout function.
2. Each `view` pane runs `viewer.py <session>/<name>.tty <name> --tty --sync <session>`. The viewer opens a PTY and symlinks `<name>.tty` to it. Because the writer is a real terminal, pwndbg sees the pane's size (and `isatty`), and the viewer keeps the PTY size in sync when the pane is resized.
3. pwndbg's `contextoutput` redirects each section into that TTY. `viewer.py` treats a clear-screen sequence as a new frame: the finished one is sealed into history and the new one is drawn.
4. Viewers in the session bind `<session>/<name>.sock` and send each other history and toggle events over those Unix datagram sockets, which is how [sync](#sync) works.
5. On exit or layout switch, panes are killed, the session directory is deleted and context output is reset.

`viewer.py` also works standalone, on a FIFO:

```sh
mkfifo /tmp/demo.fifo
python3 viewer.py /tmp/demo.fifo demo --follow '►' --mouse --wrap

# in another terminal
echo hello > /tmp/demo.fifo
```

or with `--tty`, which creates a PTY linked at the given path instead:

```sh
python3 viewer.py /tmp/demo.tty demo --tty --follow '►' --mouse --wrap

# in another terminal
echo hello > /tmp/demo.tty
```

Options:

| Option | Description |
| --- | --- |
| `-f`, `--follow REGEX` | Center a match of `REGEX` on every update |
| `--follow-mode {buffer,line,last}` | `buffer` (first match in the whole text, may span lines)</br>`line` (first matching line, faster), `last` (last matching line, fastest) |
| `--follow-live` | Leave history when a new frame arrives |
| `-m`, `--mouse` | Capture the mouse |
| `-w`, `--wrap` | Wrap long lines |
| `-s`, `--sync DIR` | Sync history and toggles with the other viewers in `DIR` |
| `-t`, `--tty` | Create a PTY linked at `PATH` instead of reading a FIFO |

## Files

```
gdbinit.py            GDB entry point
tuimux.py             Commands, layouts, area management
viewer.py             Standalone FIFO/PTY viewer used in each area
simple.gdbinit        Example config: simple
simple_code.gdbinit   Example config: simple_code
custom.gdbinit        Example config: user-defined layout
```

## Troubleshooting

- **Nothing happens / "requires GDB to run inside mux".** GDB must run inside tmux, with pwndbg loaded first and `tuimux layout <name>` in your config.
- **Can't use the terminal's mouse features.** Press `m` to toggle mouse capture off.
- **The view keeps jumping back.** Follow is on. Press `f` to pause it; `ff` re-centers.
- **The history jumps.** That is `follow_live`. Pass `follow_live=False` to `view()` in your layout to stay put.
- **History is empty.** Make sure `set context-clear-screen on` is in your config; without it there are no frame boundaries.
- **Stale session directories after a crash.** `rm -rf /tmp/tuimux-*`
- **warning: GDB: Failed to set controlling terminal: Operation not permitted.** [splitmind](https://github.com/jerdna-regeiz/splitmind) recommends ignoring this warning.

## Acknowledgements

- [kittydbg](https://github.com/k4lizen/kittydbg): gdbinit config using kitty tiling to use the empty space in pwndbg output.
- [pwnmux](https://github.com/joaogodinho/pwnmux): pwndbg and tmux config with multiple panes for different contexts.
- [splitmind](https://github.com/jerdna-regeiz/splitmind): python module to set up a layout of splits for presented information.
- [hyperpwn](https://github.com/bet4it/hyperpwn): hyper plugin to set up a flexible GDB GUI frontend with GEF, pwndbg or peda.
