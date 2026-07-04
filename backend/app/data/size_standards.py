"""Parser for the §121.201 NAICS size-standards table.

The regulation text embeds the entire table as one giant paragraph of
whitespace-separated tokens: repeating `<naics_code> <title...> <threshold>`
groups, where the threshold is either `$X.X` (annual receipts, in millions of
dollars) or a bare integer/comma-number (employee count). This is a
best-effort regex parse over that paragraph -- good enough to make E1
deterministic for the NAICS codes that actually show up in the mock data.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

_TABLE_START_MARKER = "Small Business Size Standards by NAICS"
_TABLE_END_MARKER = "Footnotes"

# One NAICS row: a 6-digit code, then a title (letters/punctuation, no bare
# digits that would be a threshold), then a dollar threshold or a bare
# employee-count number.
_ROW_RE = re.compile(
    r"(?P<code>\d{6})\s+"
    r"(?P<title>[A-Za-z][^$\d]*?)\s+"
    r"(?P<threshold>\$[\d,]+(?:\.\d+)?|\d{1,3}(?:,\d{3})*)"
    r"(?=\s|\$|\d{6}|$)"
)


class SizeStandard(TypedDict):
    type: str  # "receipts" | "employees"
    threshold: float
    title: str


def load_size_standards(path: str | Path) -> dict[str, SizeStandard]:
    """Parse the §121.201 table into {naics_code: {type, threshold, title}}."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")

    start = text.find(_TABLE_START_MARKER)
    if start == -1:
        logger.warning("Could not locate §121.201 NAICS table marker; returning empty table")
        return {}
    end = text.find(_TABLE_END_MARKER, start)
    if end == -1:
        end = len(text)
    table_text = text[start:end]

    standards: dict[str, SizeStandard] = {}
    for match in _ROW_RE.finditer(table_text):
        code = match.group("code")
        title = " ".join(match.group("title").split()).strip(" .")
        raw_threshold = match.group("threshold")
        if raw_threshold.startswith("$"):
            kind = "receipts"
            value = float(raw_threshold.replace("$", "").replace(",", ""))
        else:
            kind = "employees"
            value = float(raw_threshold.replace(",", ""))
        if not title:
            continue
        standards[code] = {"type": kind, "threshold": value, "title": title}

    logger.info("Parsed %d NAICS size standards from %s", len(standards), path)
    return standards
