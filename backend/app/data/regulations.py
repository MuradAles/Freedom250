"""Title 13 CFR Chapter I chunker + citation index + resolver + keyword search.

The source file (`data/title-13-chapter-i.txt`) marks section boundaries with
lines like `Sec. 120.110 What businesses are ineligible...` and part boundaries
with lines like `PART 120--BUSINESS LOANS`. We split on those markers to build
one chunk per section, keyed by the literal citation string that follows
`Sec. ` (e.g. `120.110`, `121.201`, even oddities like `113.3-1`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches "Sec. 120.110 What businesses are ineligible..." at the start of a line.
_SECTION_RE = re.compile(r"^Sec\.\s+(\d+)\.(\S+)\s+(.*)$", re.MULTILINE)
# Matches "PART 120--BUSINESS LOANS" (also handles "PART 1-100--[RESERVED]").
_PART_RE = re.compile(r"^PART\s+(\S+)--(.*)$", re.MULTILINE)

# Strip these prefixes/decorations when normalizing a user-supplied citation.
_CITATION_PREFIX_RE = re.compile(r"^\s*13\s*CFR\s*(part|chapter\s+i)?\s*", re.IGNORECASE)
_SEC_LABEL_RE = re.compile(r"^\s*(§+|section|sec\.?)\s*", re.IGNORECASE)
_TRAILING_SUBSECTION_RE = re.compile(r"(\([\w-]+\))+\s*$")


@dataclass
class RegulationChunk:
    """One Title 13 section, keyed by its literal citation (e.g. '120.110')."""

    part: str
    part_title: str
    section: str  # full citation, e.g. "120.110"
    heading: str
    text: str


@dataclass
class RegulationIndex:
    chunks: list[RegulationChunk] = field(default_factory=list)
    by_citation: dict[str, RegulationChunk] = field(default_factory=dict)

    def resolve(self, raw_citation: str) -> RegulationChunk | None:
        """Normalize a messy citation string and look it up.

        Handles inputs like "13 CFR § 120.110(b)", "§120.110", "120.110",
        "Sec. 120.110", "Section 120.110(b)(1)".
        """
        if not raw_citation:
            return None
        candidate = raw_citation.strip()
        candidate = _CITATION_PREFIX_RE.sub("", candidate)
        candidate = _SEC_LABEL_RE.sub("", candidate)
        candidate = candidate.strip()
        # Peel off trailing subsection parens repeatedly, e.g. "120.110(b)(1)" -> "120.110".
        while True:
            new_candidate = _TRAILING_SUBSECTION_RE.sub("", candidate).strip()
            if new_candidate == candidate:
                break
            candidate = new_candidate
        candidate = candidate.strip(" .")
        if candidate in self.by_citation:
            return self.by_citation[candidate]
        # Fallback: maybe they passed just the bit after the part, e.g. "110" for part 120.
        # Try a direct match against the tail after the dot across all sections.
        for chunk in self.chunks:
            if chunk.section.split(".", 1)[-1] == candidate:
                return chunk
        return None

    def keyword_search(self, query: str, limit: int = 5) -> list[RegulationChunk]:
        """Very simple keyword fallback: score chunks by term overlap in heading/text."""
        terms = [t for t in re.findall(r"[a-zA-Z0-9]+", query.lower()) if len(t) > 2]
        if not terms:
            return []
        scored: list[tuple[int, RegulationChunk]] = []
        for chunk in self.chunks:
            haystack = f"{chunk.heading} {chunk.text}".lower()
            score = sum(haystack.count(term) for term in terms)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [chunk for _score, chunk in scored[:limit]]


def load_regulations(path: str | Path) -> RegulationIndex:
    """Parse the Title 13 text file into an in-memory section index."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")

    # Collect part boundaries so we can attribute each section to a part/title.
    part_markers = [(m.start(), m.group(1), m.group(2).strip()) for m in _PART_RE.finditer(text)]

    def part_for_offset(offset: int) -> tuple[str, str]:
        current = ("", "")
        for start, part_no, part_title in part_markers:
            if start <= offset:
                current = (part_no, part_title)
            else:
                break
        return current

    section_matches = list(_SECTION_RE.finditer(text))
    index = RegulationIndex()
    for i, match in enumerate(section_matches):
        part_prefix, rest, heading = match.groups()
        section_citation = f"{part_prefix}.{rest}"
        body_start = match.end()
        body_end = section_matches[i + 1].start() if i + 1 < len(section_matches) else len(text)
        body = text[body_start:body_end].strip()
        part_no, part_title = part_for_offset(match.start())
        chunk = RegulationChunk(
            part=part_no or part_prefix,
            part_title=part_title,
            section=section_citation,
            heading=heading.strip(),
            text=body,
        )
        index.chunks.append(chunk)
        # Later duplicate citations (rare) overwrite earlier ones; good enough for a demo index.
        index.by_citation[section_citation] = chunk

    logger.info("Parsed %d Title 13 sections from %s", len(index.chunks), path)
    return index
