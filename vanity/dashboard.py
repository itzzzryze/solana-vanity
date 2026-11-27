"""Interactive virtual-terminal dashboard for the Solana search."""
from collections import deque
from datetime import datetime
import os
from shutil import get_terminal_size
import sys
from time import monotonic, sleep

from .patterns import CAP

ESC = "\x1b["
AURORA = ("38;5;141", "38;5;86", "38;5;244", "38;5;220")


def duration(seconds):
    hours, seconds = divmod(int(max(0, seconds)), 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def visible(text, width):
    safe = "".join(char if char.isprintable() else " " for char in str(text))
    return safe if len(safe) <= width else safe[:max(width-1, 0)] + "…"


class Dashboard:
    def __init__(self, patterns):
        self.pattern_rows = [(index, pattern.label, CAP) for index, pattern in enumerate(patterns)]
        self.enabled = False
        self.console = None
        self.input_mode = None
        self.events = deque(maxlen=5)
        self.history = deque(maxlen=20)
        self.selected = 0
        self.detail_start = 0
        self.panel = "table"
        self.state = "Preparing search"
        self.folder = None
        self.device = ""
        self.hits = {}
        self.active_seconds = 0.0
        self.elapsed = 0.0
        self.checked = 0
        self.checkpoint = 0
        self.speed = 0.0
        self.samples = deque()
        self.last_draw = float("-inf")
        self.last_rate_point = float("-inf")
        self.pending_escape = ""

    def __enter__(self):
        if not sys.stdin.isatty() or not sys.stdout.isatty() or os.environ.get("TERM") == "dumb":
            return None
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            console = ctypes.WinDLL("kernel32", use_last_error=True)
            console.GetStdHandle.argtypes = [wintypes.DWORD]
            console.GetStdHandle.restype = wintypes.HANDLE
            console.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            console.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            handle = console.GetStdHandle(-11 & 0xffffffff)
            mode = wintypes.DWORD()
            if not console.GetConsoleMode(handle, ctypes.byref(mode)):
                return None
            if not console.SetConsoleMode(handle, mode.value | 0x0005):
                return None
            self.console = console, handle, mode.value
        else:
            import termios
            import tty

            fd = sys.stdin.fileno()
            self.input_mode = fd, termios.tcgetattr(fd)
            tty.setcbreak(fd)
        try:
            sys.stdout.write(ESC + "?1049h" + ESC + "?25l")
            sys.stdout.flush()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        self.enabled = True
        return self

    def __exit__(self, *_error):
        try:
            if self.enabled:
                sys.stdout.write(ESC + "0m" + ESC + "?25h" + ESC + "?1049l")
                sys.stdout.flush()
        finally:
            if self.console:
                console, handle, mode = self.console
                console.SetConsoleMode(handle, mode)
            if self.input_mode:
                import termios

                fd, mode = self.input_mode
                termios.tcsetattr(fd, termios.TCSADRAIN, mode)
            self.enabled = False

    def event(self, message):
        self.events.append(message)
        self.draw(force=True)

    def init_segment(self, offset):
        self.samples.clear()
        self.samples.append((0.0, offset))
        self.speed = 0.0
        self.last_rate_point = float("-inf")

    def update(self, elapsed, checked, saved):
        self.elapsed = elapsed
        self.checked, self.checkpoint = checked, saved
        if elapsed - self.last_rate_point >= .25:
            self.samples.append((elapsed, checked))
            while len(self.samples) > 2 and self.samples[1][0] <= elapsed - 2:
                self.samples.popleft()
            earlier, before = self.samples[0]
            self.speed = (checked - before) / (elapsed - earlier) if elapsed > earlier else 0
            if self.speed:
                self.history.append(self.speed)
            self.last_rate_point = elapsed
        self.draw()

    def finish_segment(self, elapsed):
        self.active_seconds += elapsed
        self.elapsed = 0.0
        self.speed = 0.0

    def draw(self, force=False):
        now = monotonic()
        if not force and now - self.last_draw < .25:
            return
        self.last_draw = now
        try:
            columns, lines = os.get_terminal_size(sys.stdout.fileno())
        except OSError:
            columns, lines = get_terminal_size((90, 28))
        columns, lines = columns or 90, lines or 28
        width = max(1, columns - 1)
        accent, success, dim, warning = AURORA

        def style(value, color):
            return ESC + f"{color}m{value}" + ESC + "0m"

        def line(value, color=None):
            clipped = visible(value, width)
            return style(clipped, color) if color else clipped

        rows = self.pattern_rows
        filled = sum(bool(self.hits.get(key)) for key, _, _ in rows)
        found = sum(len(self.hits.get(key, ())) for key, _, _ in rows)
        rate = f"{self.speed:,.0f}/sec" if self.speed else "—"
        out = [
            line(f" {datetime.now():%H:%M:%S}   {self.state.upper()}   {self.device}", dim),
            line(" ━" + "━"*max(0, width-2), accent),
            line(f" RUNTIME {duration(self.active_seconds + self.elapsed)}   CURRENT {rate}", success),
            line(f" {self.checked:,} checked   {found} saved   {filled}/{len(rows)} patterns matched"),
        ]
        if width < 57:
            out[2] = line(f" {duration(self.active_seconds + self.elapsed)}  {rate}", success)
            out[3] = line(f" {self.checked:,} checked · {filled}/{len(rows)} found")
        if lines < 8:
            out = [out[2]]
        elif lines < 13:
            out = [out[0], out[2], out[3]]
        if lines >= 25:
            graph = ""
            if self.history:
                maximum = max(self.history)
                graph = "   Rate " + "".join("▁▂▃▄▅▆▇█"[min(7, int(rate / maximum * 8))]
                                            for rate in self.history)
            out.append(line(f" Checkpoint {self.checkpoint:,}{graph}", dim))

        reserve = 3 if lines >= 13 else 1
        remaining = max(1, lines - len(out) - reserve)
        if self.panel == "help":
            out.append(line(" CONTROLS", accent))
            out.extend(line(info) for info in (
                " ↑/↓ or J/K   Select a pattern",
                " Enter        Show saved addresses",
                " Esc          Back to the pattern list",
                " P            Pause / resume search",
                " O            Open results folder",
                " Q / Ctrl+C   Save and exit",
                " Benchmarks run separately and leave checkpoints alone.",
            )[:max(0, remaining-1)])
        elif self.panel == "details":
            key, label, _ = rows[self.selected]
            out.append(line(f" SAVED ADDRESSES  /  {label}", accent))
            hits = self.hits.get(key, ())
            if not hits:
                out.append(line(" No saved matches yet.", dim))
            capacity = max(0, remaining-1)
            self.detail_start = min(self.detail_start, max(0, len(hits)-capacity))
            for i, (_, address) in enumerate(hits[self.detail_start:self.detail_start+capacity], self.detail_start+1):
                out.append(line(f" {i:>2}  {address}", success))
        else:
            out.append(line(" PATTERN                         SAVED          STATUS", dim))
            capacity = max(0, remaining-1)
            first = max(0, min(self.selected, len(rows) - capacity)) if capacity < len(rows) else 0
            for i, (key, label, cap) in enumerate(rows[first:first+capacity], first):
                count = len(self.hits.get(key, ()))
                bar_size = 10 if width >= 60 else 5
                full = round(bar_size * count / cap)
                bar = "━"*full + "·"*(bar_size-full)
                status = "FULL" if count >= cap else "FOUND" if count else "SEARCH"
                mark = "›" if i == self.selected else " "
                color = success if count else accent if i == self.selected else dim
                label_width = max(8, min(width - (26 if width >= 60 else 15), 62))
                padded = visible(label, label_width).ljust(label_width)
                out.append(line(f"{mark} {padded} {count:>2}/{cap:<2} {bar}  {status}", color))

        if self.events and lines >= 13 and len(out) < lines - 2:
            out.append(line(f" {self.events[-1]}", warning))
        controls = " ↑↓ Select · Enter Details · P Pause · O Folder · ? Help · Q Quit"
        if width < 74:
            controls = " ↑↓ · Enter · P Pause · O · ? Help · Q Quit"
        if lines >= 13:
            out.append(line(" ─" + "─"*max(0, width-2), dim))
        out.append(line(controls, accent))
        # Erase each line on redraw, then remove any stale rows below the current view.
        sys.stdout.write(ESC + "H" + (ESC + "K\r\n").join(out[:lines]) + ESC + "K" + ESC + "J")
        sys.stdout.flush()

    def keys(self):
        if sys.platform == "win32":
            import msvcrt

            while msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in ("\x00", "\xe0"):
                    key = {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
                yield key.lower()
        else:
            import select

            fd = sys.stdin.fileno()
            if select.select([fd], [], [], 0)[0]:
                self.pending_escape += os.read(fd, 64).decode("ascii", errors="ignore")
            while self.pending_escape:
                value = self.pending_escape
                if value.startswith(("\x1b[A", "\x1b[B")):
                    self.pending_escape = value[3:]
                    yield "up" if value[2] == "A" else "down"
                elif value == "\x1b[":
                    break
                else:
                    self.pending_escape = value[1:]
                    yield value[0].lower()

    def poll(self, pause, running):
        changed = False
        for key in self.keys():
            changed = True
            if key in ("q", "\x03"):
                pause.request(0, None)
                return "quit"
            if key == "p" and self.state != "Complete":
                if running:
                    pause.requested = True
                    self.state = "Saving"
                else:
                    return "resume"
            elif key in ("up", "k"):
                if self.panel == "details":
                    self.detail_start = max(0, self.detail_start-1)
                else:
                    self.selected = (self.selected - 1) % len(self.pattern_rows)
            elif key in ("down", "j"):
                if self.panel == "details":
                    self.detail_start += 1
                else:
                    self.selected = (self.selected + 1) % len(self.pattern_rows)
            elif key in ("\r", "\n"):
                self.panel = "table" if self.panel == "details" else "details"
                self.detail_start = 0
            elif key == "?":
                self.panel = "table" if self.panel == "help" else "help"
            elif key == "\x1b":
                self.panel = "table"
            elif key == "o" and self.folder:
                if sys.platform == "win32":
                    try:
                        os.startfile(self.folder)
                    except OSError as error:
                        self.event(f"Could not open folder  {error}")
                else:
                    self.event(str(self.folder))
        if changed:
            self.draw(force=True)
        return None

    def wait(self, pause):
        while not pause.exit:
            action = self.poll(pause, running=False)
            if action:
                return action
            self.draw()
            sleep(.05)
        return "quit"
