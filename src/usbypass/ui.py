"""Shared terminal rendering primitives.

Keeps the TUI look consistent across the interactive menu, `usbypass
status`, `usbypass list`, etc. No third-party deps — pure ANSI with a
clean degradation path for non-TTY output and ``NO_COLOR``.
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import time
from typing import Iterable


# ---------------------------------------------------------------------------
# Color core
# ---------------------------------------------------------------------------


def color_enabled() -> bool:
    """Return True iff it is safe to emit ANSI escapes."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("USBYPASS_NO_COLOR"):
        return False
    # Respect dumb terminals.
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def _wrap(code: str, text: str) -> str:
    if not color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


# Neutral styles
def bold(t: str) -> str:   return _wrap("1", t)
def dim(t: str) -> str:    return _wrap("2", t)
def italic(t: str) -> str: return _wrap("3", t)
def under(t: str) -> str:  return _wrap("4", t)

# 8-color palette
def red(t: str) -> str:     return _wrap("31", t)
def green(t: str) -> str:   return _wrap("32", t)
def yellow(t: str) -> str:  return _wrap("33", t)
def blue(t: str) -> str:    return _wrap("34", t)
def magenta(t: str) -> str: return _wrap("35", t)
def cyan(t: str) -> str:    return _wrap("36", t)
def white(t: str) -> str:   return _wrap("37", t)

# Bright variants
def bred(t: str) -> str:    return _wrap("91", t)
def bgreen(t: str) -> str:  return _wrap("92", t)
def byellow(t: str) -> str: return _wrap("93", t)
def bblue(t: str) -> str:   return _wrap("94", t)
def bmagenta(t: str) -> str:return _wrap("95", t)
def bcyan(t: str) -> str:   return _wrap("96", t)

# Semantic helpers — use these preferentially so themes are trivial.
OK      = bgreen
WARN    = byellow
ERR     = bred
INFO    = bcyan
MUTED   = dim
ACCENT  = bmagenta
TITLE   = bold
KEY     = bcyan  # keyboard shortcut letters


# ---------------------------------------------------------------------------
# Layout primitives
# ---------------------------------------------------------------------------


def term_width(default: int = 80, cap: int = 100) -> int:
    try:
        w = shutil.get_terminal_size((default, 24)).columns
    except OSError:
        w = default
    return max(40, min(w, cap))


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences so we can compute visible widths."""
    import re
    return re.sub(r"\033\[[0-9;]*m", "", text)


def visible_len(text: str) -> int:
    return len(strip_ansi(text))


def hr(char: str = "─", width: int | None = None) -> str:
    if width is None:
        width = term_width()
    return dim(char * width)


def box_top(title: str, width: int | None = None) -> str:
    width = width or term_width()
    label = f" {title} "
    visible = visible_len(label)
    if visible + 4 > width:
        visible = width - 4
        label = label[:visible]
    left = "╭─" + label
    right = "─" * (width - visible_len(left) - 1) + "╮"
    return dim("╭─") + bold(label) + dim(right[1:])  # keep ends dim, title bold


def box_bottom(width: int | None = None) -> str:
    width = width or term_width()
    return dim("╰" + "─" * (width - 2) + "╯")


def section(title: str, width: int | None = None) -> str:
    """Render a ── SECTION ── divider centered in the terminal."""
    width = width or term_width()
    label = f" {title} "
    pad = max(0, width - len(label))
    left = pad // 2
    right = pad - left
    return dim("━" * left) + bold(label) + dim("━" * right)


def center(text: str, width: int | None = None) -> str:
    width = width or term_width()
    vis = visible_len(text)
    if vis >= width:
        return text
    pad = (width - vis) // 2
    return " " * pad + text


def kv(label: str, value: str, *, label_width: int = 14) -> str:
    """Render a single `label  value` line with fixed-width aligned label."""
    pad = " " * max(0, label_width - visible_len(label))
    return f"  {dim(label)}{pad}  {value}"


def bullet(text: str, glyph: str = "•") -> str:
    return f"  {dim(glyph)} {text}"


def rule(char: str = "·", width: int | None = None) -> str:
    width = width or term_width()
    return dim(char * width)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    if n < 0:
        return "?"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    i = 0
    f = float(n)
    while f >= 1024.0 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    if i == 0:
        return f"{int(f)} {units[i]}"
    if f >= 100:
        return f"{f:.0f} {units[i]}"
    if f >= 10:
        return f"{f:.1f} {units[i]}"
    return f"{f:.2f} {units[i]}"


def fmt_relative(ts: float | None) -> str:
    """Render a Unix timestamp as "2 minutes ago"."""
    if ts is None:
        return "?"
    now = time.time()
    delta = now - ts
    if delta < 0:
        return "in the future"
    if delta < 10:
        return "just now"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        m = int(delta / 60)
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if delta < 86400:
        h = int(delta / 3600)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = int(delta / 86400)
    return f"{d} day{'s' if d != 1 else ''} ago"


def fmt_absolute(ts: float | None) -> str:
    if ts is None:
        return "?"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


# ---------------------------------------------------------------------------
# Glyphs — swap-out fallback on non-UTF8 terminals
# ---------------------------------------------------------------------------


def _utf8_ok() -> bool:
    enc = (sys.stdout.encoding or "").lower()
    if "utf" in enc:
        return True
    return False


if _utf8_ok():
    GLYPH_BULLET   = "•"
    GLYPH_DOT_ON   = "●"
    GLYPH_DOT_OFF  = "○"
    GLYPH_CHECK    = "✓"
    GLYPH_CROSS    = "✗"
    GLYPH_ARROW    = "→"
    GLYPH_WARN     = "⚠"
    GLYPH_KEY      = "🔑"
    GLYPH_LOCK     = "🔒"
    GLYPH_USB      = "⎘"
else:  # pragma: no cover — legacy terminals
    GLYPH_BULLET   = "*"
    GLYPH_DOT_ON   = "*"
    GLYPH_DOT_OFF  = "o"
    GLYPH_CHECK    = "y"
    GLYPH_CROSS    = "x"
    GLYPH_ARROW    = "->"
    GLYPH_WARN     = "!"
    GLYPH_KEY      = "k"
    GLYPH_LOCK     = "#"
    GLYPH_USB      = "#"


# ---------------------------------------------------------------------------
# Screen control
# ---------------------------------------------------------------------------


def clear_screen() -> None:
    if color_enabled():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
    else:
        # Fall back to a blank line separator so output stays readable.
        sys.stdout.write("\n" * 2)
