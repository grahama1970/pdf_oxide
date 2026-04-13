"""Width-aware line breaking. We control the breaks, not Platypus."""
from __future__ import annotations
from dataclasses import dataclass
from reportlab.pdfbase.pdfmetrics import stringWidth
from .fonts import FontSpec


@dataclass
class BrokenLine:
    """A single line after breaking."""
    text: str
    width: float
    words: list[tuple[str, float, float]]  # (word, x_offset, width)


@dataclass
class BrokenBlock:
    """A block after line breaking."""
    lines: list[BrokenLine]
    total_height: float


def break_text_into_lines(
    text: str,
    max_width: float,
    font: FontSpec,
    leading: float | None = None,
) -> BrokenBlock:
    """Break text into lines that fit within max_width.

    Returns BrokenBlock with line-by-line breakdown including word positions.
    """
    if leading is None:
        leading = font.size * 1.2

    words = text.split()
    if not words:
        return BrokenBlock(lines=[], total_height=0)

    lines: list[BrokenLine] = []
    current_words: list[str] = []
    current_width = 0.0
    space_width = stringWidth(" ", font.name, font.size)

    for word in words:
        word_width = stringWidth(word, font.name, font.size)

        # Check if word fits on current line
        needed = word_width
        if current_words:
            needed += space_width

        if current_width + needed <= max_width:
            current_words.append(word)
            current_width += needed
        else:
            # Emit current line
            if current_words:
                lines.append(_build_line(current_words, font, space_width))

            # Start new line with current word
            if word_width <= max_width:
                current_words = [word]
                current_width = word_width
            else:
                # Word too long - force it on its own line (will overflow)
                lines.append(_build_line([word], font, space_width))
                current_words = []
                current_width = 0.0

    # Emit final line
    if current_words:
        lines.append(_build_line(current_words, font, space_width))

    total_height = len(lines) * leading
    return BrokenBlock(lines=lines, total_height=total_height)


def _build_line(words: list[str], font: FontSpec, space_width: float) -> BrokenLine:
    """Build a BrokenLine with word positions."""
    word_data: list[tuple[str, float, float]] = []
    x = 0.0

    for i, word in enumerate(words):
        if i > 0:
            x += space_width
        word_width = stringWidth(word, font.name, font.size)
        word_data.append((word, x, word_width))
        x += word_width

    line_text = " ".join(words)
    line_width = x

    return BrokenLine(text=line_text, width=line_width, words=word_data)


def estimate_line_count(text: str, max_width: float, font: FontSpec) -> int:
    """Quick estimate of how many lines text will need."""
    avg_char_width = font.size * 0.5
    chars_per_line = max(1, int(max_width / avg_char_width))
    return max(1, (len(text) + chars_per_line - 1) // chars_per_line)
