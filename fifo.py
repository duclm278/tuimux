#!/usr/bin/env python3
"""Terminal viewer for a FIFO.

usage: fifo.py FIFO [TITLE] [-f REGEX] [--follow-mode MODE] [-m] [-w]

  -f, --follow REGEX  on update, center a match of REGEX (colors ignored)
  --follow-mode MODE  buffer: first match in the whole unwrapped text, may span
                      lines (default); line: first matching line, faster;
                      last: last matching line, scanned from the end, fastest
  -m, --mouse         capture mouse: wheel scroll, drag to copy (OSC 52 / tmux)
  -w, --wrap          wrap long lines

Keys: hjkl/arrows ±10, HJKL/Shift+arrows ±20, PgUp/PgDn, 0/Home, $/End,
      g/G top/bottom, f/m/w toggle follow/mouse/wrap
      / ? search down/up (incremental; Enter accepts, Esc cancels), n/N next/prev,
      Esc clears the highlight
Scroll: vertical, +Shift horizontal
Title: TITLE Y%,X% (% of content reached; 100% = fully visible)
Corner: active flags (briefly after start or a toggle), search position [i/n]
"""

import argparse
import base64
import codecs
import os
import re
import select
import signal
import subprocess
import sys
import termios
import time
import tty
import unicodedata
from bisect import bisect_left, bisect_right
from contextlib import contextmanager, suppress
from functools import cache
from itertools import accumulate, islice
from typing import NamedTuple

STDIN = 0
STEP_SMALL, STEP_LARGE = 10, 20  # keyboard scroll steps
WHEEL_X, WHEEL_Y = 6, 3  # scroll per wheel tick
FAR = 1 << 32  # clamped when applied
BURST_DELAY = 0.016  # max wait before drawing FIFO data
FLAGS_TIMEOUT = 2.0  # seconds the corner flags stay visible
ESC_DELAY = 0.05  # wait for the rest of a split escape sequence
MAX_RAW = 8 << 20  # retained input cap when there are no frame markers
FRAME_OVERLAP = 16  # rescan window for a split frame marker
MAX_COUNT = 999  # search matches counted for [i/n]
TAB_WIDTH = 8

SGR_RESET, SGR_INVERT = "\x1b[0m", "\x1b[7m"
STYLE_MATCH, STYLE_CURRENT = "\x1b[30;43m", "\x1b[30;48;5;208m"  # like nvim hlsearch
CLEAR_SCREEN, CLEAR_TO_EOL = "\x1b[2J", "\x1b[K"
HIDE_CURSOR, SHOW_CURSOR = "\x1b[?25l", "\x1b[?25h"
AUTOWRAP_OFF, AUTOWRAP_ON = "\x1b[?7l", "\x1b[?7h"
ALT_SCREEN_ON, ALT_SCREEN_OFF = "\x1b[?1049h", "\x1b[?1049l"
TITLE_PUSH, TITLE_POP = "\x1b[22;2t", "\x1b[23;2t"
SYNC_BEGIN, SYNC_END = "\x1b[?2026h", "\x1b[?2026l"
MOUSE_ENABLE, MOUSE_DISABLE = "\x1b[?1002h\x1b[?1006h", "\x1b[?1006l\x1b[?1002l"

# Mouse report button bits (wheel: 0 up, 1 down, 2 left, 3 right; +4 Shift)
MOUSE_BUTTON, MOUSE_DRAG, MOUSE_WHEEL = 3, 32, 64
WHEEL_FORWARD, WHEEL_HORIZONTAL = 1, 6

# A clear-screen sequence starts a new frame.
FRAME_START = re.compile(r"(?:\x1b\[(?:H|2J|3J|J))+")

# Keeps SGR sequences (group 1) and visible characters (group 2); drops the rest.
TOKEN = re.compile(
    r"(\x1b\[[0-9;:]*m)"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC
    r"|\x1b\[[0-?]*[ -/]*[@-~]"  # other CSI
    r"|\x1b[ -/]*[0-~]?"  # other escapes
    r"|[\x00-\x08\x0a-\x1f\x7f-\x9f]"  # control characters
    r"|([^\x00-\x08\x0a-\x1f\x7f-\x9f])",
    re.S,
)
# Mouse reports come first so they are never split.
INPUT_EVENT = re.compile(
    r"\x1b\[<\d+;\d+;\d+[Mm]|\x1b\[[0-9;]*[A-Za-z~]|\x1bO[A-Z]|.", re.S
)
INCOMPLETE_EVENT = re.compile(r"\x1b(?:\[[0-9;<?]*|O)?$")


def expand_keys(bindings):
    return {key: value for keys, value in bindings.items() for key in keys.split()}


SCROLL_KEYS = expand_keys(  # key -> (dx, dy)
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
ACTION_KEYS = expand_keys(  # key -> Viewer method name
    {
        "0 \x1b[H \x1bOH \x1b[1~": "line_start",
        "$ \x1b[F \x1bOF \x1b[4~": "line_end",
        "\x1b[6~": "page_down",
        "\x1b[5~": "page_up",
        "/": "search_down",
        "?": "search_up",
        "n": "next_match",
        "N": "previous_match",
        "\x1b": "escape",
        "g": "top",
        "G": "bottom",
        "f": "toggle_follow",
        "m": "toggle_mouse",
        "w": "toggle_wrap",
    }
)


# ---- helpers ----------------------------------------------------------------


def write(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def terminal_size():
    """(cols, rows), at least 1x1. Asks the tty: $COLUMNS/$LINES can be stale."""
    size = os.get_terminal_size(STDIN)
    return max(1, size.columns), max(1, size.lines)


@cache
def char_width(char):
    """Cells a character occupies: 0, 1 or 2."""
    if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def copy_to_clipboard(text):
    """Copy via OSC 52, and into tmux's buffer when inside tmux."""
    write(f"\x1b]52;c;{base64.b64encode(text.encode()).decode()}\x07")
    if os.environ.get("TMUX"):
        for flags in (["-w"], []):  # -w needs tmux >= 3.2
            with suppress(OSError):
                run = subprocess.run(
                    ["tmux", "load-buffer", *flags, "-"],
                    input=text.encode(),
                    capture_output=True,
                )
                if run.returncode == 0:
                    return


def percent_covered(edge, total):
    """How much of `total` is covered once the visible border reaches `edge`."""
    return min(100, round(100 * edge / total)) if total else 100


def to_match(ends, line, start, end):
    """Match for string offsets [start, end) of a line, as cell columns.

    `ends` holds the string offset where each cell ends (cells may be several
    characters).
    """
    return Match(
        line, bisect_right(ends, start), bisect_right(ends, max(end - 1, start)) + 1
    )


def drain(fd):
    """Read everything available from a non-blocking fd."""
    data = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            return data
        if not chunk:
            return data
        data += chunk


def render_row(tokens, first_col, width, highlight=()):
    """Render columns [first_col, first_col + width) of a token row.

    Tokens are SGR sequences or cells ("" is a wide character's second half).
    `highlight` is a list of (start, end, sgr) column spans; earlier ones win.
    """
    last_col = first_col + width
    style_at = {}
    for start, end, sgr in reversed(highlight):
        style_at.update(
            dict.fromkeys(range(max(start, first_col), min(end, last_col)), sgr)
        )
    out, col, style, content_sgr = [], 0, "", ""
    for index, token in enumerate(tokens):
        if token.startswith("\x1b"):
            reset = token[2:-1].split(";")[0] in ("", "0")
            content_sgr = token if reset else content_sgr + token
            out.append(token + style)
            continue
        if first_col <= col < last_col:
            if token == "":
                char = " " if col == first_col else ""  # left half clipped
            elif col == last_col - 1 and tokens[index + 1 : index + 2] == [""]:
                char = " "  # right half would be clipped
            else:
                char = token
            if (wanted := style_at.get(col, "")) != style:
                out.append(SGR_RESET + content_sgr + wanted)  # restore content style
                style = wanted
            out.append(char)
        col += 1
        if col >= last_col:
            break
    return "".join(out) + SGR_RESET


# ---- data types -------------------------------------------------------------


class Line(NamedTuple):
    tokens: list  # SGR sequences and cells
    cells: list  # one per terminal column


class Row(NamedTuple):
    """A visual row: a slice of a Line (all of it unless wrapping)."""

    tokens: list
    start: int  # column of the Line where the row begins
    cells: list
    continued: bool  # continues the previous row's Line
    line: int


class Layout(NamedTuple):
    rows: list
    content_width: int
    cols: int
    height: int


class Match(NamedTuple):
    line: int
    start: int  # cell columns
    end: int


class Selection:
    """Drag selection between two (row, col) cells, in either direction."""

    def __init__(self, cell):
        self.anchor = self.end = cell

    @property
    def is_empty(self):
        return self.anchor == self.end

    def span(self, row, length):
        """Selected columns (start, end) of a visual row, or None."""
        (first_row, first_col), (last_row, last_col) = sorted([self.anchor, self.end])
        if not first_row <= row <= last_row:
            return None
        return (
            first_col if row == first_row else 0,
            last_col + 1 if row == last_row else length,
        )


# ---- content: raw text -> lines -> visual rows ------------------------------


class Content:
    def __init__(self):
        self.raw = ""
        self.lines = []
        self.width = 0  # widest row of the last rows() result
        self.epoch = 0  # bumped when earlier lines are dropped
        self.version = 0  # bumped on every feed
        self._done = []  # parsed newline-terminated lines
        self._done_end = 0  # offset in `raw` past the last of them
        self._dirty = True
        self._rows_key = None  # (wrap, cols) of the cached rows
        self._rows = []

    def feed(self, text):
        scan_from = max(0, len(self.raw) - FRAME_OVERLAP)
        self.raw += text
        last = None
        for last in FRAME_START.finditer(self.raw, scan_from):
            pass
        if last:
            self._discard(last.end())
        elif len(self.raw) > MAX_RAW:  # keep the newest half
            cut = len(self.raw) - MAX_RAW // 2
            newline = self.raw.find("\n", cut)
            self._discard(newline + 1 if newline >= 0 else cut)
        self._dirty = True
        self.version += 1

    def _discard(self, upto):
        self.raw = self.raw[upto:]
        self._done, self._done_end = [], 0
        self.epoch += 1

    def rows(self, cols, wrap):
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
            self._done.extend(map(self._parse_line, chunk.split("\n")))
            self._done_end = end
        lines = [*self._done]
        if tail := self.raw[end:]:
            lines.append(self._parse_line(tail))
        while lines and not lines[-1].tokens:
            lines.pop()
        self.lines = lines

    def find(self, pattern, mode):
        """First match for a follow mode (see --follow-mode)."""
        self._parse()
        if mode == "buffer":
            return self._find_buffer(pattern)
        count = len(self.lines)
        for index in range(count) if mode == "line" else reversed(range(count)):
            if matches := self.matches(pattern, index):
                return matches[0]

    def search(self, pattern, line, col, forward=True):
        """Next match after (line, col), wrapping around the buffer."""
        self._parse()
        count = len(self.lines)
        for step in range(count + 1 if count else 0):
            index = (line + step if forward else line - step) % count
            matches = self.matches(pattern, index)
            if step == 0:  # the origin line: only the part beyond the origin
                matches = [
                    m for m in matches if (m.start > col if forward else m.start < col)
                ]
            if matches:
                return matches[0 if forward else -1]

    def all_matches(self, pattern):
        self._parse()
        for index in range(len(self.lines)):
            yield from self.matches(pattern, index)

    def _find_buffer(self, pattern):
        if not self.lines:
            return None
        texts = ["".join(line.cells) for line in self.lines]
        match = pattern.search("\n".join(texts))
        if not match:
            return None
        starts = [*accumulate((len(t) + 1 for t in texts), initial=0)]
        index = bisect_right(starts, match.start()) - 1
        ends = [*accumulate(map(len, self.lines[index].cells))]
        offset = starts[index]
        return to_match(ends, index, match.start() - offset, match.end() - offset)

    def matches(self, pattern, index):
        """All matches in one line."""
        cells = self.lines[index].cells
        ends = [*accumulate(map(len, cells))]
        return [
            to_match(ends, index, m.start(), m.end())
            for m in pattern.finditer("".join(cells))
        ]

    @staticmethod
    def _parse_line(raw_line):
        # Carriage return (progress bars): keep the last non-empty segment.
        if "\r" in raw_line:
            raw_line = next((p for p in reversed(raw_line.split("\r")) if p), "")

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
                if width == 0:  # combining: attach to the previous cell
                    if last_cell is not None:
                        tokens[last_cell] += char
                    continue
                tokens.append(char)
                last_cell = len(tokens) - 1
                if width == 2:
                    tokens.append("")
                col += width
        return Line(tokens, [t for t in tokens if not t.startswith("\x1b")])

    def _plain_rows(self):
        return [Row(l.tokens, 0, l.cells, False, i) for i, l in enumerate(self.lines)]

    def _wrap_lines(self, cols):
        rows = []
        for index, line in enumerate(self.lines):
            cells, total, start = line.cells, len(line.cells), 0
            while True:
                end = min(start + cols, total)
                if end < total and cells[end] == "" and end > start + 1:
                    end -= 1  # don't split a wide character
                rows.append(Row(line.tokens, start, cells[start:end], start > 0, index))
                if end >= total:
                    break
                start = end
        return rows


# ---- viewer: scrolling, selection, input, drawing ---------------------------


class Viewer:
    def __init__(
        self, title, follow=None, follow_mode="buffer", mouse=False, wrap=False
    ):
        """`follow` is a compiled regex whose first matching row stays in view."""
        self.title = title
        self.follow_pattern = follow
        self.following = follow is not None
        self.follow_mode = follow_mode
        self.mouse_captured = mouse
        self.wrap = wrap
        self.content = Content()
        self.x = self.y = 0  # first visible column / row
        self.selection = None
        self.dragging = False
        self._follow_pending = self.following
        self._pending_input = ""  # start of an escape sequence split across reads
        self._last_screen = None
        self._last_title = None
        self.search_input = None  # text typed at the prompt; None when not searching
        self.search_pattern = None  # last accepted search
        self.forward = True  # direction of the last search
        self.match = None  # current search match
        self.highlighting = False  # show search matches (Esc turns it off)
        self._match_epoch = 0
        self._candidate = None  # pattern compiled from the typed text
        self._row_matches = {}  # line -> matches, rebuilt on every draw
        self._count_key, self._found = None, []  # cached matches for [i/n]
        self._saved = (0, 0, None)  # view and match to restore on cancel
        self._origin = (0, 0)  # (line, col) the typed search starts from
        self._flags_until = time.monotonic() + FLAGS_TIMEOUT  # corner flags deadline

    def feed(self, text):
        self.content.feed(text)
        self._follow_pending = self.following
        if self.match and self._match_epoch != self.content.epoch:
            self.match = None  # its line no longer exists
        if not self.dragging:
            self.selection = None

    def invalidate(self):
        """Force a full redraw (e.g. after a resize)."""
        self._last_screen = None
        self.selection = None  # rows re-wrap

    def update_layout(self):
        """Clamp scrolling to the content and describe the layout."""
        cols, height = terminal_size()
        rows = self.content.rows(cols, self.wrap)
        width = self.content.width
        self.x = max(0, min(self.x, width - cols))
        self.y = max(0, min(self.y, len(rows) - height))
        return Layout(rows, width, cols, height)

    def scroll_to(self, match):
        """Center `match` vertically and make it horizontally visible."""
        layout = self.update_layout()
        key = lambda row: (row.line, row.start)
        index = max(
            0, bisect_right(layout.rows, (match.line, match.start), key=key) - 1
        )
        col = match.start - layout.rows[index].start
        self.y = index - layout.height // 2
        if not self.x <= col < self.x + layout.cols:
            self.x = max(0, col - layout.cols // 2)

    # ---- selection ----------------------------------------------------------

    @property
    def _highlight_pattern(self):
        if self.search_input is not None:
            return self._candidate
        return self.search_pattern if self.highlighting else None

    def row_highlight(self, row_index, row):
        """Highlight spans (start, end, sgr) of a row, in line coordinates."""
        spans = []
        if self.selection and (span := self.selection.span(row_index, len(row.cells))):
            spans.append((span[0] + row.start, span[1] + row.start, SGR_INVERT))
        if pattern := self._highlight_pattern:
            if row.line not in self._row_matches:
                self._row_matches[row.line] = self.content.matches(pattern, row.line)
            found = self._row_matches[row.line]
            spans += [(m.start, m.end, STYLE_CURRENT) for m in found if m == self.match]
            spans += [(m.start, m.end, STYLE_MATCH) for m in found if m != self.match]
        return spans

    def finish_selection(self):
        """Copy the selection (a plain click selects nothing)."""
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
            joined = (  # soft-wrapped pieces of one line are joined verbatim
                following
                and following.continued
                and self.selection.span(index + 1, len(following.cells))
            )
            chunks.append(text if joined else text.rstrip() + "\n")
        copy_to_clipboard("".join(chunks).removesuffix("\n"))

    # ---- search -------------------------------------------------------------

    def _search_origin(self, forward):
        """Where a search starts: the current match, else the top visible row."""
        if self.match:
            return self.match.line, self.match.start
        rows = self.update_layout().rows
        if self.y < len(rows):
            return rows[self.y].line, rows[self.y].start - forward
        return 0, -1

    def _go(self, match):
        self.match, self._match_epoch = match, self.content.epoch
        if match:
            self.scroll_to(match)

    def _step(self, forward):
        if self.search_pattern:
            self.highlighting = True
            origin = self._search_origin(forward)
            self._go(self.content.search(self.search_pattern, *origin, forward))

    def _start_search(self, forward):
        self.search_input, self.forward = "", forward
        self._saved = (self.y, self.x, self.match)
        self._origin = self._search_origin(forward)

    def _update_search(self):
        """Search for the typed text from the origin, restoring the view first."""
        self.y, self.x, _ = self._saved
        self.match = self._candidate = None
        if not self.search_input:
            return
        try:
            self._candidate = re.compile(self.search_input)
        except re.error:
            return
        self._go(self.content.search(self._candidate, *self._origin, self.forward))

    def _accept_search(self):
        typed, self.search_input = self.search_input, None
        if not typed:
            self._step(self.forward)  # empty prompt: repeat the last search
        elif self._candidate:
            self.search_pattern, self.highlighting = self._candidate, True

    def escape(self):
        """Cancel the prompt, or clear the search highlight."""
        if self.search_input is not None:
            self.cancel_search()
        else:
            self.highlighting = False

    def cancel_search(self):
        if self.search_input is not None:
            self.search_input = None
            self.y, self.x, self.match = self._saved

    def _search_key(self, event):
        if event in ("\r", "\n"):
            self._accept_search()
        elif event == "\x1b" or (event in ("\x7f", "\x08") and not self.search_input):
            self.cancel_search()
        elif event in ("\x7f", "\x08", "\x15"):  # backspace, Ctrl-U
            self.search_input = "" if event == "\x15" else self.search_input[:-1]
            self._update_search()
        elif len(event) == 1 and event.isprintable():
            self.search_input += event
            self._update_search()

    # ---- input --------------------------------------------------------------

    @property
    def has_pending_input(self):
        return bool(self._pending_input)

    def discard_pending_input(self):
        if self._pending_input == "\x1b":  # a lone ESC
            self.escape()
        self._pending_input = ""

    def handle_input(self, data):
        data = self._pending_input + data
        self._pending_input = ""
        if incomplete := INCOMPLETE_EVENT.search(data):  # wait for the rest
            self._pending_input = incomplete.group(0)
            data = data[: incomplete.start()]

        for found in INPUT_EVENT.finditer(data):
            event = found.group(0)
            if self.search_input is not None:
                self._search_key(event)
            elif event.startswith("\x1b[<"):
                code, col, row = map(int, event[3:-1].split(";"))
                self.handle_mouse(code, col, row, pressed=event[-1] == "M")
            elif event in SCROLL_KEYS:
                dx, dy = SCROLL_KEYS[event]
                self.x += dx
                self.y += dy
            elif event in ACTION_KEYS:
                getattr(self, ACTION_KEYS[event])()
            self.update_layout()

    def handle_mouse(self, code, col, row, pressed):
        """Handle one SGR mouse report (`col`, `row` are 1-based)."""
        if code & MOUSE_WHEEL:
            step = 1 if code & WHEEL_FORWARD else -1
            if code & WHEEL_HORIZONTAL:
                self.x += step * WHEEL_X
            else:
                self.y += step * WHEEL_Y
        elif code & MOUSE_BUTTON == 0:  # left button
            cell = (self.y + row - 1, self.x + col - 1)
            if code & MOUSE_DRAG:
                if self.selection:
                    self.selection.end = cell
            elif pressed:
                self.selection, self.dragging = Selection(cell), True
            elif self.selection:
                self.selection.end = cell
                self.finish_selection()

    # ---- actions ------------------------------------------------------------

    def line_start(self):
        self.x = 0

    def line_end(self):
        self.x = FAR

    def page_up(self):
        self.y -= max(1, terminal_size()[1] - 1)

    def page_down(self):
        self.y += max(1, terminal_size()[1] - 1)

    def search_down(self):
        self._start_search(True)

    def search_up(self):
        self._start_search(False)

    def next_match(self):
        self._step(self.forward)

    def previous_match(self):
        self._step(not self.forward)

    def top(self):
        self.y = 0

    def bottom(self):
        self.y = FAR

    def toggle_follow(self):
        if self.follow_pattern:
            self.following = self._follow_pending = not self.following
            self._show_flags()

    def toggle_mouse(self):
        self.mouse_captured = not self.mouse_captured
        self.selection, self.dragging = None, False
        write(MOUSE_ENABLE if self.mouse_captured else MOUSE_DISABLE)
        self._show_flags()

    def toggle_wrap(self):
        # Keep the top visible logical line in place across re-wrapping.
        layout = self.update_layout()
        top_line = layout.rows[self.y].line if self.y < len(layout.rows) else 0
        self.wrap, self.x, self.selection = not self.wrap, 0, None
        rows = self.content.rows(layout.cols, self.wrap)
        self.y = next((i for i, row in enumerate(rows) if row.line >= top_line), 0)
        self._show_flags()

    # ---- drawing ------------------------------------------------------------

    def draw(self):
        self._row_matches = {}
        layout = self.update_layout()
        if self._follow_pending:
            self._follow_pending = False
            if self.follow_pattern:
                if match := self.content.find(self.follow_pattern, self.follow_mode):
                    self.scroll_to(match)
                layout = self.update_layout()
        screen = self._visible_rows(layout)
        if self.search_input is not None:
            prompt = ("/" if self.forward else "?") + self.search_input
            prompt = prompt[max(0, len(prompt) - layout.cols + 1) :]
            screen[-1] = f"{prompt}{SGR_INVERT} {SGR_RESET}{CLEAR_TO_EOL}"
        elif corner := self._corner(layout):
            col = layout.cols - len(corner) + 1
            screen[-1] += f"\x1b[{layout.height};{col}H{SGR_INVERT}{corner}{SGR_RESET}"
        output = ""
        if screen != self._last_screen:
            output += self._screen_update(screen)
            self._last_screen = screen
        if (title := self._window_title(layout)) != self._last_title:
            output += f"\x1b]2;{title}\x1b\\"
            self._last_title = title
        if output:
            write(SYNC_BEGIN + output + SYNC_END)

    def _visible_rows(self, layout):
        """One rendered string per terminal row."""
        first = self.y
        screen = [
            render_row(
                row.tokens, row.start + self.x, layout.cols, self.row_highlight(i, row)
            )
            + CLEAR_TO_EOL
            for i, row in enumerate(layout.rows[first : first + layout.height], first)
        ]
        return screen + [CLEAR_TO_EOL] * (layout.height - len(screen))

    def _screen_update(self, screen):
        """Escape sequences that update the terminal to `screen`."""
        previous = self._last_screen
        full = previous is None or len(previous) != len(screen)
        return (CLEAR_SCREEN if full else "") + "".join(
            f"\x1b[{i + 1};1H{row}"
            for i, row in enumerate(screen)
            if full or previous[i] != row
        )

    @property
    def flags_deadline(self):
        """When the corner flags hide, or None if already hidden."""
        return self._flags_until

    def _show_flags(self):
        self._flags_until = time.monotonic() + FLAGS_TIMEOUT

    def _count_text(self):
        """Position of the current match, e.g. "[2/5]"."""
        key = (self.search_pattern, self.content.version)
        if self._count_key != key:
            all_matches = self.content.all_matches(self.search_pattern)
            self._found = list(islice(all_matches, MAX_COUNT + 1))
            self._count_key = key
        found = self._found
        if not found:
            return "[0/0]"
        total = f">{MAX_COUNT}" if len(found) > MAX_COUNT else len(found)
        i = bisect_left(found, self.match) if self.match else len(found)
        index = i + 1 if i < len(found) and found[i] == self.match else "?"
        return f"[{index}/{total}]"

    def _corner_flags(self):
        """Active flags ("-" if none) while visible, else ""."""
        if self._flags_until is None or time.monotonic() >= self._flags_until:
            self._flags_until = None
            return ""
        flags = "|".join(
            flag
            for flag, active in (
                ("f", self.following),
                ("m", self.mouse_captured),
                ("w", self.wrap),
            )
            if active
        )
        return flags or "-"

    def _corner(self, layout):
        """Bottom-right text: flags (briefly) and the search position."""
        parts = [self._corner_flags()]
        if self.highlighting and self.search_pattern:
            parts.append(self._count_text())
        text = " ".join(p for p in parts if p)
        return f" {text} "[-layout.cols :] if text else ""

    def _window_title(self, layout):
        """Title with the scroll position."""
        if layout.content_width <= layout.cols and len(layout.rows) <= layout.height:
            return self.title
        y = percent_covered(self.y + layout.height, len(layout.rows))
        x = percent_covered(self.x + layout.cols, layout.content_width)
        return f"{self.title} {y}%,{x}%"


# ---- program ----------------------------------------------------------------


@contextmanager
def terminal_session(mouse):
    """Enter viewer mode; restore the terminal on exit."""
    saved_attrs = termios.tcgetattr(STDIN)
    tty.setcbreak(STDIN)
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


def event_loop(viewer, fifo, wake_read):
    fifo_decoder = codecs.getincrementaldecoder("utf-8")("replace")
    stdin_decoder = codecs.getincrementaldecoder("utf-8")("ignore")
    draw_at = esc_at = None  # deadlines: pending FIFO draw / split escape

    viewer.draw()
    while True:
        flags_at = viewer.flags_deadline
        deadlines = [d for d in (draw_at, esc_at, flags_at) if d is not None]
        timeout = max(0.0, min(deadlines) - time.monotonic()) if deadlines else None
        ready, _, _ = select.select([STDIN, fifo, wake_read], [], [], timeout)
        now = time.monotonic()
        redraw = False

        if fifo in ready:
            viewer.feed(fifo_decoder.decode(drain(fifo)))
            if draw_at is None:  # fixed deadline: a steady stream can't starve drawing
                draw_at = now + BURST_DELAY
        if wake_read in ready:  # resized
            drain(wake_read)
            viewer.invalidate()
            redraw = True
        if STDIN in ready:
            data = os.read(STDIN, 8192)
            if not data:
                break
            viewer.handle_input(stdin_decoder.decode(data))
            esc_at = now + ESC_DELAY if viewer.has_pending_input else None
            redraw = True

        if draw_at is not None and now >= draw_at:
            redraw = True
        if flags_at is not None and now >= flags_at:  # hide corner flags
            redraw = True
        if esc_at is not None and now >= esc_at:  # lone ESC
            viewer.discard_pending_input()
            esc_at, redraw = None, True
        if redraw:
            viewer.draw()
            draw_at = None


def parse_args():
    parser = argparse.ArgumentParser(description="Terminal viewer for a FIFO.")
    parser.add_argument("fifo")
    parser.add_argument("title", nargs="?", default="view")
    parser.add_argument("-f", "--follow", metavar="REGEX", help="center first match")
    parser.add_argument(
        "--follow-mode",
        choices=("buffer", "line", "last"),
        default="buffer",
        help="how --follow searches (see docstring)",
    )
    parser.add_argument("-m", "--mouse", action="store_true", help="capture mouse")
    parser.add_argument("-w", "--wrap", action="store_true", help="wrap long lines")
    return parser.parse_args()


def main():
    args = parse_args()
    follow = None
    if args.follow is not None:
        try:
            follow = re.compile(args.follow, re.MULTILINE)
        except re.error as error:
            sys.exit(f"fifo.py: invalid --follow regex {args.follow!r}: {error}")
    viewer = Viewer(args.title, follow, args.follow_mode, args.mouse, args.wrap)

    fifo = os.open(args.fifo, os.O_RDWR | os.O_NONBLOCK)  # RDWR: never EOF
    wake_read, wake_write = os.pipe()
    os.set_blocking(wake_read, False)
    os.set_blocking(wake_write, False)
    signal.set_wakeup_fd(wake_write)  # any handled signal wakes select()
    signal.signal(signal.SIGWINCH, lambda *_: None)
    for sig in (signal.SIGTERM, signal.SIGHUP):  # exit normally to restore the tty
        signal.signal(sig, lambda signum, _: sys.exit(128 + signum))

    with suppress(KeyboardInterrupt), terminal_session(args.mouse):
        event_loop(viewer, fifo, wake_read)


if __name__ == "__main__":
    main()
