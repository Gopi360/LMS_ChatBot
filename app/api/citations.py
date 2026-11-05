"""Takes the citation markers out of the text a student sees. This changes the display only: the
citation check in app/generation/chatbot.py has already run, and the API still returns the sources."""
from __future__ import annotations

import re
from typing import Iterable, Match


def strip_citations(text: str, cited: Iterable[int]) -> str:
    """The text without the [n] markers of the cited passages. Only those numbers are removed, so
    "[2026]" and "[NEW]" in a course name stay. With nothing cited, the text is returned untouched."""
    numbers = set(cited)
    if not numbers:
        return text

    def drop(match: Match[str]) -> str:
        return "" if int(match.group(1)) in numbers else match.group(0)

    text = re.sub(r"\[(\d{1,2})\]", drop, text)
    # Only punctuation that ends a word moves back, so " .NET" keeps its space.
    text = re.sub(r"[ \t]+([.,;:!?])(?=\s|$)", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)         # "hours [1]. [2]." but never an ellipsis
    text = re.sub(r"(?<=\S)[ \t]{2,}", " ", text)          # not at the line start: that is list indentation
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
