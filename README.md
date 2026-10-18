# tuimux

A customizable TUI+tmux setup for [pwndbg](https://github.com/pwndbg/pwndbg). It splits your tmux window into panes and streams each pwndbg context section into its own scrollable TUI viewer.

![Layout simple](assets/simple.png)

## Features

- **Switchable layouts.** `tuimux layout NAME` builds panes at any prompt and tears down the old ones.
- **Real viewers.** Scroll, wrap, mouse select, SGR colors preserved.
- **Vim-like search.** `/` and `?` with incremental regex matching and a `[i/n]` counter.
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

Start `tmux`, then run `gdb ./binary` (or `pwndbg ./binary` depending on your setup). Panes appear when `tuimux layout` runs. `-y` applies the layout's suggested GDB options, `-n` skips them, and omitting both asks.

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
| `/` , `?` | Search down / up (regex) |
| `n` , `N` | Next / previous match |
| `Esc` | Cancel the search prompt, or clear highlights |
| `w` | Toggle line wrap (off by default) |
| `f` | Toggle follow (panes with follow only) |
| `m` | Toggle mouse capture (release it to use the terminal's native selection) |

**Mouse:** the wheel or a two-finger swipe scrolls vertically (hold Shift for horizontal). Dragging selects and copies on release.

### Search

- `/` or `?` opens a prompt at the bottom. Matches are found as you type; `Enter` accepts, `Esc` cancels. An empty `Enter` repeats the last search.
- Patterns are Python regexes. Matches are highlighted, the current one in orange, and the corner shows `[i/n]`.

### Follow

A pane created with `follow="REGEX"` re-centers a matching line on every update. Any pane can follow any regex. For example, the built-in layouts follow `►` in `backtrace` (and in `disasm` and `code` for the `*_code` layouts), so the current frame, instruction and source line stay in the middle as you step.

- `f` pauses or resumes follow. Follow overrides manual scrolling on the next update, so pause it to look around.
- **Tip:** if scrolling makes you lose focus on the current line, press `ff` to pause and resume, which re-centers it.
- Matching ignores colors. Examples: `►`, `^\s*►`, `(?i)error`.
- `view()` also takes `follow_mode`, and standalone `fifo.py` takes `--follow-mode`: `buffer` (default, first match in the whole unwrapped text, may span lines), `line` (first matching line, faster) or `last` (last matching line, fastest).

**Tip:** set `context-<section>-lines` high. The viewer scrolls, so you can browse freely around the current instruction, stack or call chain while follow keeps `►` centered. The `*_code` layouts will suggest this for you.

### Status

- **Title:** `disasm 64%,100%` shows how much of the content's bottom and right edge is visible (100% = fully visible). Hidden when everything fits.
- **Corner (bottom right):** active flags `f` (follow), `m` (mouse capture), `w` (wrap), shown for about 2 seconds after start or a toggle (`-` means none), plus the search position `[i/n]` while highlights are on.

### tmux bindings

Root-table bindings (no prefix needed):

| Key | Action |
| --- | --- |
| `Alt+h/j/k/l` / `Alt+Arrows` | Move between panes |
| `Alt+z` | Zoom or unzoom the pane |

On the first layout, tuimux also turns on `mouse` and `pane-border-status top` for the window and installs these bindings. Pane titles are set to the section names (`main` for the GDB pane).

### GDB commands

| Command | Description |
| --- | --- |
| `tuimux layout` | Show the current and available layouts |
| `tuimux layout NAME [-y\|--yes\|-n\|--no]` | Switch to layout `NAME` |
| `tuimux toggle mouse` | Toggle mouse capture in all viewer panes at once |
| `tuimux toggle wrap` | Toggle wrapping in all viewer panes at once |

Commands support tab completion.

#### Layout command

Switching a layout creates its panes, redirects the pwndbg sections into them and then redraws with `context`.

If the layout has suggested options (for example `context-disasm-lines`), they are handled like this:

- `-y` / `--yes`: apply them silently.
- `-n` / `--no`: skip them.
- Neither flag: list them and ask `Apply these options? [Y/n]` (interactive use only).

Suggested options per layout:

| Layout | `context-code-lines` | `context-stack-lines` | `context-disasm-lines` |
| --- | --- | --- | --- |
| `simple` | 14 | 1024 | 10 |
| `simple_code` | 1024 | 1024 | 1024 |
| `splitmind` | 18 | 1024 | 10 |
| `splitmind_code` | 1024 | 1024 | 1024 |

## Layouts

| Layout | Config | Description |
| --- | --- | --- |
| `simple` | `simple.gdbinit` | Default |
| `simple_code` | `simple_code.gdbinit` | Default plus a separate `code` pane |
| `splitmind` | | [splitmind](https://github.com/jerdna-regeiz/splitmind)-style, with input/output and ipython panes |
| `splitmind_code` | | Splitmind plus a `code` pane |

`simple`:

```txt
┌───────────────────────┬───────────────────────┐
│ disasm                │ regs                  │
│                       ├───────────────────────┤
│                       │ stack                 │
├───────────────────────┴───────────┬───────────┤
│ main (gdb)                        │ backtrace │
└───────────────────────────────────┴───────────┘
```

`simple_code`:

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

`splitmind_code`:

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

![Layout splitmind_code](assets/splitmind_code.png)

In the splitmind layouts, the input/output pane prints its tty; in GDB run `tty /dev/pts/<num>` to redirect the binary's I/O there.

### Writing your own

A layout receives the main (GDB) pane and splits panes off it. After sourcing `gdbinit.py`, these are available in `python` blocks:

```python
@layout(opts=None)                                                                              # decorator; opts = suggested GDB options
L, D, U, R                                                                                      # sides: left, down, up, right
view(base, side, size, name, follow=None, follow_mode=None, mouse=True, wrap=False, **options)  # viewer pane showing section `name`
open(base, side, size, name, cmd="cat -")                                                       # plain pane running any command
```

`size` is a tmux `-l` value like `"30%"`. Extra `**options` go to pwndbg's `contextoutput`. Call `.add("section")` on a view to show more sections in it; unmapped sections stay in the GDB pane (see [pwndbg's sections](https://pwndbg.re/stable/configuration/config/#context-sections)).

Example (`custom.gdbinit`):

```python
source /path/to/tuimux/gdbinit.py
python
@layout(opts={
    "context-code-lines": 1024,
    "context-stack-lines": 1024,
    "context-disasm-lines": 1024,
})
def custom(ma):
    ba = view(ma, R, "25%", "backtrace", follow="►")
    di = view(ma, U, "80%", "disasm", follow="►")
    io = open(di, R, "55%", "input/output", cmd="tty; tail -f /dev/null")
    st = view(io, U, "75%", "stack")
    rg = view(st, U, "60%", "regs").add("legend")
    ip = open(ba, D, "30%", "ipython", cmd="ipython3")
    co = view(di, D, "55%", "code", follow="►")
end
tuimux layout custom -y
```

Each pane is split relative to its `base`, so order matters.

## How it works

1. `tuimux layout NAME` finds a live multiplexer (tmux), tears down any previous layout and runs the layout function.
2. Each `view` pane gets a FIFO at `/tmp/view-<pid>-<name>.fifo` and runs `fifo.py <fifo> <name>`.
3. pwndbg's `contextoutput` redirects each section into its FIFO. `fifo.py` treats a clear-screen sequence as a new frame and redraws.
4. On exit or layout switch, panes are killed, FIFOs deleted and context output reset.

`fifo.py` also works standalone:

```sh
mkfifo /tmp/demo.fifo
python3 fifo.py /tmp/demo.fifo demo --follow '►' --mouse --wrap

# in another terminal
echo hello > /tmp/demo.fifo
```

Options: `-f/--follow REGEX`, `--follow-mode {buffer,line,last}`, `-m/--mouse`, `-w/--wrap`. Follow modes: `buffer` (first match in the whole text, may span lines), `line` (first matching line, faster), `last` (last matching line, fastest).

## Files

```
gdbinit.py            GDB entry point: commands, layouts, pane management
fifo.py               Standalone FIFO viewer used in each pane
simple.gdbinit        Example config: simple
simple_code.gdbinit   Example config: simple_code
custom.gdbinit        Example config: user-defined layout
```

## Troubleshooting

- **Nothing happens / "requires GDB to run inside mux".** GDB must run inside tmux, with pwndbg loaded first and `tuimux layout NAME` in your config.
- **Can't select text with the terminal's native selection.** Press `m` in the pane.
- **The view keeps jumping back.** Follow is on. Press `f` to pause it; `ff` re-centers.
- **Stale FIFOs after a crash.** `rm /tmp/view-*.fifo`
- **warning: GDB: Failed to set controlling terminal: Operation not permitted.** [splitmind](https://github.com/jerdna-regeiz/splitmind) recommends ignoring this warning.

## Acknowledgements

- [kittydbg](https://github.com/k4lizen/kittydbg): gdbinit config using kitty tiling to use the empty space in pwndbg output.
- [pwnmux](https://github.com/joaogodinho/pwnmux): pwndbg and tmux config with multiple panes for different contexts.
- [splitmind](https://github.com/jerdna-regeiz/splitmind): Python module to set up a layout of splits for presented information.
