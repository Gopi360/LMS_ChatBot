"""Takes the citation markers out of the text a student sees. This changes the display only: the
citation check in app/generation/chatbot.py has already run, and the API still returns the sources."""
from __future__ import annotations

import re
from typing import Iterable, Match

MARKERS = r"\[\d{1,2}\](?:[ \t]*[,;]?[ \t]*\[\d{1,2}\])*"
# After a full stop, the stop that follows the marker goes too: "hours [1]. [2]." has one sentence.
AFTER_A_STOP = re.compile(rf"(?<=\.)[ \t]*{MARKERS}[ \t]*\.")
# Not straight after a word, because "arr[1]" is code. A space before punctuation goes with the marker.
MARKER = re.compile(rf"[ \t]*(?<!\w)(?:\({MARKERS}\)|{MARKERS})(?:[ \t]+(?=[.,;:!?](?:\s|$)))?")


def strip_citations(text: str, cited: Iterable[int]) -> str:
    """The text without the [n] markers of the cited passages. Only those numbers are removed, so
    "[2026]" and "[NEW]" in a course name stay. Only the space around a marker is tidied, because
    the rest is course content: "cd .." must stay as it is. With nothing cited, nothing changes."""
    numbers = set(cited)
    if not numbers:
        return text

    def drop(match: Match[str]) -> str:
        found = {int(n) for n in re.findall(r"\d+", match.group(0))}
        return "" if found <= numbers else match.group(0)

    text = MARKER.sub(drop, AFTER_A_STOP.sub(drop, text))
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
