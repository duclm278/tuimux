#!/usr/bin/env python3
"""
usage: fifo.py FIFO [TITLE] [-w, --wrap | --no-wrap] [-m, --mouse | --no-mouse]

Keys:
  h/j/k/l or Arrows : ±10 cols/rows
  H/J/K/L or Shift+Arrows : ±20 cols/rows
  PgUp/PgDn : ±1 page
  0/Home, $/End : line start/end
  g/G : top/bottom
  w : toggle wrap | m : toggle mouse capture

Mouse:
  Scroll wheel : vertical scroll (+Shift for horizontal)
  2-finger swipe : vertical scroll (+Shift for horizontal)
  Drag : select and copy text (via OSC 52; also tmux buffer if in tmux)

Title format: "TITLE Y%,X%" (% of content edge reached; 100% = fully visible)
"""

import argparse
import base64
import codecs
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
import tty
import unicodedata
from contextlib import contextmanager, suppress
from functools import lru_cache
from typing import NamedTuple

# =============================================================================
# Configuration
# =============================================================================

STEP_SMALL, STEP_LARGE = 10, 20  # keyboard scroll steps
WHEEL_STEP_X, WHEEL_STEP_Y = 6, 3  # scroll per wheel tick
SCROLL_MAX = 1 << 32  # "as far as possible"; clamped when applied
BURST_DELAY = 0.016  # max seconds a FIFO update may wait before being drawn
ESC_DELAY = 0.05  # seconds to wait for the rest of a split escape sequence
MAX_RAW = 8 << 20  # cap on retained input (chars) when no frame markers appear
FRAME_OVERLAP = 16  # chars re-scanned when looking for a split frame marker
TAB_WIDTH = 8
STDIN = 0

# =============================================================================
# Terminal escape sequences
# =============================================================================

SGR_RESET = "\x1b[0m"
SGR_INVERT = "\x1b[7m"
SGR_UNINVERT = "\x1b[27m"

CLEAR_SCREEN = "\x1b[2J"
CLEAR_TO_EOL = "\x1b[K"
HIDE_CURSOR, SHOW_CURSOR = "\x1b[?25l", "\x1b[?25h"
AUTOWRAP_OFF, AUTOWRAP_ON = "\x1b[?7l", "\x1b[?7h"
ALT_SCREEN_ON, ALT_SCREEN_OFF = "\x1b[?1049h", "\x1b[?1049l"
TITLE_PUSH, TITLE_POP = "\x1b[22;2t", "\x1b[23;2t"  # save/restore window title
SYNC_BEGIN, SYNC_END = "\x1b[?2026h", "\x1b[?2026l"  # atomic screen updates

# Button-event mouse reporting (press/release/drag/wheel) with SGR encoding.
MOUSE_ENABLE = "\x1b[?1002h\x1b[?1006h"
MOUSE_DISABLE = "\x1b[?1006l\x1b[?1002l"

# Bits of the mouse report's button code.
MOUSE_BUTTON_MASK = 3
MOUSE_LEFT = 0
MOUSE_DRAG = 32
MOUSE_WHEEL = 64  # wheel codes: 0 up, 1 down, 2 left, 3 right (+4 with Shift)
MOUSE_WHEEL_DOWN_OR_RIGHT = 1
MOUSE_WHEEL_HORIZONTAL = 2 | 4  # left/right wheel, or Shift held

# =============================================================================
# Input parsing
# =============================================================================

# A "clear screen" sequence marks the start of a new frame of output.
FRAME_START = re.compile(r"(?:\x1b\[(?:H|2J|3J|J))+")

# Splits a line into SGR sequences (kept) and visible characters (kept);
# every other escape sequence and control character is matched and dropped.
TOKEN = re.compile(
    r"(\x1b\[[0-9;:]*m)"  # 1: SGR (colors, attributes)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC
    r"|\x1b\[[0-?]*[ -/]*[@-~]"  # any other CSI
    r"|\x1b[ -/]*[0-~]?"  # other escapes, e.g. ESC ( B
    r"|[\x00-\x08\x0a-\x1f\x7f-\x9f]"  # remaining control characters
    r"|([^\x00-\x08\x0a-\x1f\x7f-\x9f])",  # 2: a visible character (or tab)
    re.S,
)

# Splits keyboard input into events. The SGR mouse report comes first so that
# it is never split into stray characters.
INPUT_EVENT = re.compile(
    r"\x1b\[<\d+;\d+;\d+[Mm]|\x1b\[[0-9;]*[A-Za-z~]|\x1bO[A-Z]|.", re.S
)
# An escape sequence cut off at the end of a read; completed by the next one.
INCOMPLETE_EVENT = re.compile(r"\x1b(?:\[[0-9;<?]*|O)?$")


def expand_keys(bindings):
    """{"key key ...": value} -> {key: value}: several keys can share a value."""
    return {key: value for keys, value in bindings.items() for key in keys.split()}


# key -> (dx, dy)
SCROLL_KEYS = expand_keys(
    {
        "l \x1b[C \x1bOC": (STEP_SMALL, 0),
        "h \x1b[D \x1bOD": (-STEP_SMALL, 0),
        "j \x1b[B \x1bOB": (0, STEP_SMALL),
        "k \x1b[A \x1bOA": (0, -STEP_SMALL),
        "L \x1b[1;2C": (STEP_LARGE, 0),
        "H \x1b[1;2D": (-STEP_LARGE, 0),
        "J \x1b[1;2B": (0, STEP_LARGE),
        "K \x1b[1;2A": (0, -STEP_LARGE),
    }
)

# key -> action name (see Viewer.perform)
ACTION_KEYS = expand_keys(
    {
        "0 \x1b[H \x1bOH \x1b[1~": "line_start",
        "$ \x1b[F \x1bOF \x1b[4~": "line_end",
        "\x1b[6~": "page_down",
        "\x1b[5~": "page_up",
        "g": "top",
        "G": "bottom",
        "w": "toggle_wrap",
        "m": "toggle_mouse",
    }
)

# =============================================================================
# Helpers
# =============================================================================


def write(text):
    """Write to the terminal immediately."""
    sys.stdout.write(text)
    sys.stdout.flush()


def terminal_size():
    """(cols, rows) of the terminal, always at least 1x1.

    Asks the tty directly: shutil.get_terminal_size() prefers $COLUMNS/$LINES,
    which can be stale and would defeat resize handling.
    """
    try:
        size = os.get_terminal_size(STDIN)
    except OSError:
        size = shutil.get_terminal_size()
    return max(1, size.columns), max(1, size.lines)


@lru_cache(maxsize=None)
def char_width(char):
    """Terminal cells a character occupies: 0 (combining), 1 or 2 (wide)."""
    if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def copy_to_tmux(text):
    """Load `text` into tmux's paste buffer (and tmux's clipboard, if supported).

    `-w` (tmux >= 3.2) also forwards to the terminal clipboard; older versions
    reject it, so retry without. Failures are ignored: OSC 52 is the fallback.
    """
    for flags in (["-w"], []):
        with suppress(OSError):
            result = subprocess.run(
                ["tmux", "load-buffer", *flags, "-"],
                input=text.encode(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return


def copy_to_clipboard(text):
    """Copy via OSC 52 and, when running inside tmux, into tmux's buffer too."""
    encoded = base64.b64encode(text.encode()).decode()
    write(f"\x1b]52;c;{encoded}\x07")
    if os.environ.get("TMUX"):
        copy_to_tmux(text)


def window_title_sequence(title):
    """OSC sequence that sets the terminal window title."""
    return f"\x1b]2;{title}\x1b\\"


def percent_covered(edge, total):
    """How much of `total` is covered once the visible border reaches `edge`."""
    return min(100, round(100 * edge / total)) if total else 100


def drain(fd):
    """Read everything currently available from a non-blocking fd."""
    data = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            return data
        if not chunk:
            return data
        data += chunk


def reverse_after(sgr, reverse):
    """Whether reverse video is on after applying the SGR sequence `sgr`."""
    params = sgr[2:-1].split(";")
    i = 0
    while i < len(params):
        param = params[i]
        if param in ("", "0", "27"):
            reverse = False
        elif param == "7":
            reverse = True
        elif param in ("38", "48", "58"):  # extended color: skip its arguments
            following = params[i + 1 : i + 2]
            i += 2 if following == ["5"] else 4 if following == ["2"] else 0
        i += 1
    return reverse


def render_row(tokens, first_col, width, highlight=None):
    """Render columns [first_col, first_col + width) of a token row.

    Tokens are SGR sequences (start with ESC) or cells: one cell per terminal
    column, where "" is the second half of a wide character. Colors are kept.
    Columns inside `highlight` (start, end) are inverted.
    """
    out, col, inverted, content_reverse = [], 0, False, False
    last_col = first_col + width
    for index, token in enumerate(tokens):
        if token.startswith("\x1b"):
            content_reverse = reverse_after(token, content_reverse)
            # An SGR reset must not drop the selection highlight.
            out.append(token + (SGR_INVERT if inverted else ""))
            continue
        if first_col <= col < last_col:
            if token == "":  # right half of a wide character
                char = " " if col == first_col else ""  # left half is clipped
            elif col == last_col - 1 and tokens[index + 1 : index + 2] == [""]:
                char = " "  # right half would be clipped
            else:
                char = token
            selected = bool(highlight) and highlight[0] <= col < highlight[1]
            if selected != inverted:
                if selected:
                    out.append(SGR_INVERT)
                elif not content_reverse:  # keep the content's own reverse video
                    out.append(SGR_UNINVERT)
                inverted = selected
            out.append(char)
        col += 1
        if col >= last_col:
            break
    return "".join(out) + SGR_RESET


# =============================================================================
# Data types
# =============================================================================


class Line(NamedTuple):
    """One logical line of input."""

    tokens: list  # SGR sequences and cells, in order
    cells: list  # the cells only: one per terminal column


class Row(NamedTuple):
    """One visual row: a slice of a Line (the whole line unless wrapping)."""

    tokens: list
    start: int  # column of the Line where this row begins
    cells: list
    continued: bool  # True if this row continues the previous row's Line
    line: int  # index of the Line this row belongs to


class Layout(NamedTuple):
    rows: list
    content_width: int
    cols: int  # terminal width
    height: int  # terminal height


class Selection:
    """A drag selection between two (row, col) cells, in either direction."""

    def __init__(self, cell):
        self.anchor = self.end = cell

    @property
    def is_empty(self):
        return self.anchor == self.end

    def span(self, row, length):
        """Selected columns (start, end) of visual row `row`, or None."""
        (first_row, first_col), (last_row, last_col) = sorted([self.anchor, self.end])
        if not first_row <= row <= last_row:
            return None
        start = first_col if row == first_row else 0
        end = last_col + 1 if row == last_row else length
        return start, end


# =============================================================================
# Content: raw text -> lines -> visual rows
# =============================================================================


class Content:
    """Text received from the FIFO, with lazily computed lines and rows."""

    def __init__(self):
        self.raw = ""
        self.lines = []
        self.width = 0  # widest row of the last rows() result
        self._done = []  # parsed lines that are complete (newline-terminated)
        self._done_end = 0  # offset in `raw` just past the last such line
        self._dirty = True
        self._rows_key = None  # (wrap, cols) the cached rows were built for
        self._rows = []

    def feed(self, text):
        scan_from = max(0, len(self.raw) - FRAME_OVERLAP)
        self.raw += text
        last = None
        for last in FRAME_START.finditer(self.raw, scan_from):
            pass
        if last:  # a new frame began: drop everything before it
            self._discard(last.end())
        elif len(self.raw) > MAX_RAW:  # no frame markers: keep only the newest half
            cut = len(self.raw) - MAX_RAW // 2
            newline = self.raw.find("\n", cut)
            self._discard(newline + 1 if newline >= 0 else cut)
        self._dirty = True

    def _discard(self, upto):
        self.raw = self.raw[upto:]
        self._done, self._done_end = [], 0

    def rows(self, cols, wrap):
        """Visual rows, wrapped to `cols` columns when `wrap` is set."""
        self._parse()
        if self._rows_key != (wrap, cols):
            self._rows = self._wrap_lines(cols) if wrap else self._plain_rows()
            self.width = max((len(row.cells) for row in self._rows), default=0)
            self._rows_key = (wrap, cols)
        return self._rows

    def _parse(self):
        if not self._dirty:
            return
        self._dirty = False
        self._rows_key, self._rows = None, []

        # Complete lines are parsed once; only the unfinished tail is redone.
        end = self.raw.rfind("\n") + 1
        if end > self._done_end:
            chunk = self.raw[self._done_end : end - 1]
            self._done.extend(self._parse_line(raw) for raw in chunk.split("\n"))
            self._done_end = end
        lines = list(self._done)
        tail = self.raw[end:]
        if tail:
            lines.append(self._parse_line(tail))
        while lines and not lines[-1].tokens:
            lines.pop()
        self.lines = lines

    @staticmethod
    def _parse_line(raw_line):
        # A carriage return rewinds to column 0 (progress bars): approximate by
        # keeping the last non-empty segment. This also handles CRLF.
        if "\r" in raw_line:
            segments = [part for part in raw_line.split("\r") if part]
            raw_line = segments[-1] if segments else ""

        tokens, col, last_cell = [], 0, None
        for match in TOKEN.finditer(raw_line):
            sgr, char = match.groups()
            if sgr:
                tokens.append(sgr)
            elif char == "\t":
                pad = TAB_WIDTH - col % TAB_WIDTH
                tokens.extend(" " * pad)
                col += pad
                last_cell = len(tokens) - 1
            elif char:
                width = char_width(char)
                if width == 0:  # combining character: attach to the previous cell
                    if last_cell is not None:
                        tokens[last_cell] += char
                    continue
                tokens.append(char)
                last_cell = len(tokens) - 1
                if width == 2:
                    tokens.append("")  # placeholder for the second column
                col += width
        cells = [token for token in tokens if not token.startswith("\x1b")]
        return Line(tokens, cells)

    def _plain_rows(self):
        return [
            Row(line.tokens, 0, line.cells, False, index)
            for index, line in enumerate(self.lines)
        ]

    def _wrap_lines(self, cols):
        rows = []
        for index, line in enumerate(self.lines):
            cells, total, start = line.cells, len(line.cells), 0
            while True:
                end = min(start + cols, total)
                # Don't split a wide character across two rows.
                if end < total and cells[end] == "" and end > start + 1:
                    end -= 1
                rows.append(Row(line.tokens, start, cells[start:end], start > 0, index))
                if end >= total:
                    break
                start = end
        return rows


# =============================================================================
# Viewer: scrolling, selection, input handling, drawing
# =============================================================================


class Viewer:
    def __init__(self, title, wrap=True, mouse=True):
        self.title = title
        self.wrap = wrap
        self.content = Content()
        self.x = self.y = 0  # scroll offset (first visible column / row)
        self.selection = None
        self.dragging = False  # left button is held down
        self.mouse_captured = mouse
        self._pending_input = ""  # start of an escape sequence split across reads
        self._last_screen = None  # rows drawn last time, for diffing
        self._last_title = None

    # ---- content ------------------------------------------------------------

    def feed(self, text):
        self.content.feed(text)
        if not self.dragging:
            self.selection = None

    def invalidate(self):
        """Force a full redraw on the next draw() (e.g. after a resize)."""
        self._last_screen = None
        self.selection = None  # rows re-wrap, so selected cells would be wrong

    # ---- layout -------------------------------------------------------------

    def update_layout(self):
        """Clamp the scroll position to the content and describe the layout."""
        cols, height = terminal_size()
        rows = self.content.rows(cols, self.wrap)
        width = self.content.width
        self.x = max(0, min(self.x, width - cols))
        self.y = max(0, min(self.y, len(rows) - height))
        return Layout(rows, width, cols, height)

    # ---- selection ----------------------------------------------------------

    def row_highlight(self, row_index, row):
        """Selected columns of a row, in line coordinates, or None."""
        span = self.selection and self.selection.span(row_index, len(row.cells))
        return span and (span[0] + row.start, span[1] + row.start)

    def finish_selection(self):
        """Copy the selected text to the clipboard (a plain click selects nothing)."""
        self.dragging = False
        if self.selection.is_empty:
            self.selection = None
            return
        rows = self.update_layout().rows
        chunks = []
        for index, row in enumerate(rows):
            span = self.selection.span(index, len(row.cells))
            if not span:
                continue
            text = "".join(row.cells[span[0] : span[1]])
            following = rows[index + 1] if index + 1 < len(rows) else None
            # Rows that are soft-wrapped pieces of one line are joined verbatim.
            if (
                following
                and following.continued
                and self.selection.span(index + 1, len(following.cells))
            ):
                chunks.append(text)
            else:
                chunks.append(text.rstrip() + "\n")
        copy_to_clipboard("".join(chunks).removesuffix("\n"))

    # ---- input --------------------------------------------------------------

    @property
    def has_pending_input(self):
        return bool(self._pending_input)

    def discard_pending_input(self):
        """Give up on an escape sequence that never got completed (a lone ESC)."""
        self._pending_input = ""

    def handle_input(self, data):
        data = self._pending_input + data
        self._pending_input = ""
        incomplete = INCOMPLETE_EVENT.search(data)
        if incomplete:  # wait for the rest instead of misreading the fragment
            self._pending_input = incomplete.group(0)
            data = data[: incomplete.start()]

        for match in INPUT_EVENT.finditer(data):
            event = match.group(0)
            if event.startswith("\x1b[<"):
                code, col, row = map(int, event[3:-1].split(";"))
                self.handle_mouse(code, col, row, pressed=event[-1] == "M")
            elif event in SCROLL_KEYS:
                dx, dy = SCROLL_KEYS[event]
                self.x += dx
                self.y += dy
            elif event in ACTION_KEYS:
                self.perform(ACTION_KEYS[event])
            self.update_layout()

    def handle_mouse(self, code, col, row, pressed):
        """Handle one SGR mouse report (`col` and `row` are 1-based)."""
        if code & MOUSE_WHEEL:
            step = 1 if code & MOUSE_WHEEL_DOWN_OR_RIGHT else -1
            if code & MOUSE_WHEEL_HORIZONTAL:
                self.x += step * WHEEL_STEP_X
            else:
                self.y += step * WHEEL_STEP_Y
            return

        if (code & MOUSE_BUTTON_MASK) != MOUSE_LEFT:
            return
        cell = (self.y + row - 1, self.x + col - 1)
        if code & MOUSE_DRAG:
            if self.selection:
                self.selection.end = cell
        elif pressed:
            self.selection = Selection(cell)
            self.dragging = True
        elif self.selection:  # left button released: the release position counts
            self.selection.end = cell
            self.finish_selection()

    def perform(self, action):
        if action == "line_start":
            self.x = 0
        elif action == "line_end":
            self.x = SCROLL_MAX
        elif action == "page_up":
            self.y -= max(1, terminal_size()[1] - 1)
        elif action == "page_down":
            self.y += max(1, terminal_size()[1] - 1)
        elif action == "top":
            self.y = 0
        elif action == "bottom":
            self.y = SCROLL_MAX
        elif action == "toggle_wrap":
            # Row numbers mean something else after re-wrapping: keep the
            # top visible logical line in place instead.
            layout = self.update_layout()
            top_line = layout.rows[self.y].line if self.y < len(layout.rows) else 0
            self.wrap, self.x, self.selection = not self.wrap, 0, None
            rows = self.content.rows(layout.cols, self.wrap)
            self.y = next((i for i, row in enumerate(rows) if row.line >= top_line), 0)
        elif action == "toggle_mouse":
            # Hand the mouse back to the terminal (or take it again).
            self.mouse_captured = not self.mouse_captured
            self.selection, self.dragging = None, False
            write(MOUSE_ENABLE if self.mouse_captured else MOUSE_DISABLE)

    # ---- drawing ------------------------------------------------------------

    def draw(self):
        layout = self.update_layout()
        output = self._changed_rows(self._visible_rows(layout))
        output += self._changed_title(layout)
        if output:
            write(SYNC_BEGIN + "".join(output) + SYNC_END)

    def _visible_rows(self, layout):
        """The screen contents: one rendered string per terminal row."""
        first, last = self.y, self.y + layout.height
        screen = [
            render_row(
                row.tokens,
                row.start + self.x,
                layout.cols,
                self.row_highlight(index, row),
            )
            for index, row in enumerate(layout.rows[first:last], first)
        ]
        return screen + [""] * (layout.height - len(screen))

    def _changed_rows(self, screen):
        """Escape sequences that bring the terminal up to date with `screen`."""
        previous = self._last_screen
        full_redraw = previous is None or len(previous) != len(screen)
        output = [CLEAR_SCREEN] if full_redraw else []
        output += [
            f"\x1b[{index + 1};1H{row}{CLEAR_TO_EOL}"
            for index, row in enumerate(screen)
            if full_redraw or previous[index] != row
        ]
        self._last_screen = screen
        return output

    def _changed_title(self, layout):
        """The OSC sequence updating the window title, if it changed."""
        flags = ("[w]" if self.wrap else "") + ("[m]" if self.mouse_captured else "")
        title = f"{self.title} {flags}" if flags else self.title

        scrollable = (
            layout.content_width > layout.cols or len(layout.rows) > layout.height
        )
        if scrollable:
            y_percent = percent_covered(self.y + layout.height, len(layout.rows))
            x_percent = percent_covered(self.x + layout.cols, layout.content_width)
            title += f" {y_percent}%,{x_percent}%"

        if title == self._last_title:
            return []
        self._last_title = title
        return [window_title_sequence(title)]


# =============================================================================
# Program setup and main loop
# =============================================================================


@contextmanager
def terminal_session(mouse):
    """Put the terminal in viewer mode (capturing the mouse if `mouse`), restoring it on exit."""
    saved_attrs = termios.tcgetattr(STDIN)
    tty.setcbreak(STDIN)
    # alternate screen, save title, no autowrap, hide cursor, clear screen,
    # (optionally) enable mouse
    write(
        ALT_SCREEN_ON
        + TITLE_PUSH
        + AUTOWRAP_OFF
        + HIDE_CURSOR
        + CLEAR_SCREEN
        + (MOUSE_ENABLE if mouse else "")
    )
    try:
        yield
    finally:
        termios.tcsetattr(STDIN, termios.TCSADRAIN, saved_attrs)
        write(MOUSE_DISABLE + AUTOWRAP_ON + SHOW_CURSOR + TITLE_POP + ALT_SCREEN_OFF)


def exit_on_signal(signum, _frame):
    """Turn termination signals into a normal exit so the terminal is restored."""
    raise SystemExit(128 + signum)


def wake_on_resize():
    """Make SIGWINCH wake select(); returns the read end of the wake-up pipe."""
    wake_read, wake_write = os.pipe()
    os.set_blocking(wake_read, False)
    os.set_blocking(wake_write, False)

    def on_winch(*_):
        with suppress(OSError):
            os.write(wake_write, b"x")

    signal.signal(signal.SIGWINCH, on_winch)
    return wake_read


def event_loop(viewer, fifo, wake_read):
    fifo_decoder = codecs.getincrementaldecoder("utf-8")("replace")
    stdin_decoder = codecs.getincrementaldecoder("utf-8")("ignore")
    draw_at = None  # deadline for drawing FIFO data that arrived but isn't drawn
    esc_at = None  # deadline for the rest of a split escape sequence

    viewer.draw()
    while True:
        deadlines = [d for d in (draw_at, esc_at) if d is not None]
        timeout = max(0.0, min(deadlines) - time.monotonic()) if deadlines else None
        ready, _, _ = select.select([STDIN, fifo, wake_read], [], [], timeout)
        now = time.monotonic()
        redraw = False

        if fifo in ready:
            viewer.feed(fifo_decoder.decode(drain(fifo)))
            if draw_at is None:  # fixed deadline: a steady stream can't starve drawing
                draw_at = now + BURST_DELAY
        if wake_read in ready:  # terminal resized
            drain(wake_read)
            viewer.invalidate()
            redraw = True
        if STDIN in ready:
            data = os.read(STDIN, 8192)
            if not data:  # stdin closed
                break
            viewer.handle_input(stdin_decoder.decode(data))
            esc_at = now + ESC_DELAY if viewer.has_pending_input else None
            redraw = True

        if draw_at is not None and now >= draw_at:
            redraw = True
        if esc_at is not None and now >= esc_at:
            viewer.discard_pending_input()
            esc_at = None
        if redraw:
            viewer.draw()
            draw_at = None


def parse_args():
    parser = argparse.ArgumentParser(description="A nice viewer for FIFO files.")
    parser.add_argument("fifo")
    parser.add_argument("title", nargs="?", default="view")
    parser.add_argument(
        "-w",
        "--wrap",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="start in wrap mode (default on; use --no-wrap to start unwrapped)",
    )
    parser.add_argument(
        "-m",
        "--mouse",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="capture mouse for scrolling/selection (default on; "
        "use --no-mouse to start with it released)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    viewer = Viewer(args.title, args.wrap, args.mouse)
    # O_RDWR so the FIFO never reports EOF when a writer closes it.
    fifo = os.open(args.fifo, os.O_RDWR | os.O_NONBLOCK)
    wake_read = wake_on_resize()
    signal.signal(signal.SIGTERM, exit_on_signal)
    signal.signal(signal.SIGHUP, exit_on_signal)
    try:
        with terminal_session(args.mouse):
            event_loop(viewer, fifo, wake_read)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
