"""Create simple document metadata records for RAG ingestion."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


TITLE_HEADING = re.compile(
    r"^TITLE\s+(?P<number>\d+)\s*(?:--|_)\s*(?P<title>.+)$"
)
CHAPTER_HEADING = re.compile(
    r"^CHAPTER\s+(?P<number>[IVXLCDM0-9]+)\s*(?:--|_)\s*(?P<title>.+)$"
)
SUBCHAPTER_HEADING = re.compile(
    r"^SUBCHAPTER\s+(?P<number>[A-Z0-9]+)\s*(?:--|_)\s*(?P<title>.+)$"
)
PART_HEADING = re.compile(
    r"^PART\s+(?P<number>\d+(?:-\d+)?[A-Z]?)"
    r"(?:\s*(?:--|_)\s*|\s+)(?P<title>.+?)(?:--Table of Contents)?$"
)
SUBPART_HEADING = re.compile(
    r"^Subpart\s+(?P<number>[A-Z0-9.]+(?:-[A-Z0-9.]+)?)"
    r"(?:\s*(?:--|_)\s*|\s+)(?P<title>.+)$"
)
SECTION_HEADING = re.compile(
    r"^Sec\.\s+(?:Sec\.\s+)?"
    r"(?P<number>\d+\.[\dA-Za-z.-]+(?:-\d+\.[\dA-Za-z.-]+)?)\s+"
    r"(?P<title>.+)$"
)
PARAGRAPH_MARKER = re.compile(r"^\((?P<marker>[A-Za-z0-9]+)\)\s*(?P<text>.*)$")
STRUCTURE_START = re.compile(
    r"^(?:TITLE\b|CHAPTER\b|SUBCHAPTER\b|PART\b|Subpart\b|Sec\.)",
    re.IGNORECASE,
)


def extract_title(file_path: str | Path) -> str:
    """Return the first meaningful line, or a title made from the filename."""
    path = Path(file_path)

    with path.open(encoding="utf-8", errors="ignore") as document:
        for line in document:
            title = line.strip().strip("[]").strip()
            if title and not set(title) <= {"-", "=", "_"}:
                return title

    return path.stem.replace("_", " ").replace("-", " ").title()


def extract_chapter_titles(file_path: str | Path) -> list[str]:
    """Return the chapter headings found in a document."""
    metadata = extract_legal_hierarchy(file_path)
    return [
        node["heading"]
        for node in _walk_nodes(metadata["hierarchy"])
        if node["type"] == "chapter"
    ]


def _walk_nodes(nodes: list[dict[str, Any]]):
    """Yield every node in a legal hierarchy."""
    for node in nodes:
        yield node
        yield from _walk_nodes(node.get("children", []))


def _clean_heading_title(title: str) -> str:
    """Normalize separators and remove table-of-contents labels."""
    title = re.sub(r"--Table of Contents$", "", title, flags=re.IGNORECASE)
    return title.replace("_", " ").strip(" -")


def _heading_node(
    node_type: str,
    number: str,
    title: str,
    heading: str,
    citation: str,
) -> dict[str, Any]:
    """Create one hierarchy node."""
    return {
        "type": node_type,
        "number": number,
        "title": _clean_heading_title(title),
        "heading": heading,
        "citation": citation,
        "children": [],
    }


def _find_or_add(
    parent: list[dict[str, Any]], node: dict[str, Any]
) -> dict[str, Any]:
    """Reuse repeated table-of-contents headings instead of duplicating them."""
    for existing in parent:
        if existing["type"] == node["type"] and existing["number"] == node["number"]:
            existing.update(
                {
                    "title": node["title"],
                    "heading": node["heading"],
                    "citation": node["citation"],
                }
            )
            return existing
    parent.append(node)
    return node


def _logical_heading(lines: list[str], index: int) -> tuple[str, int]:
    """Join wrapped structural headings and return the next unread line."""
    heading = lines[index].strip()
    if not STRUCTURE_START.match(heading):
        return heading, index + 1

    if TITLE_HEADING.match(heading) or CHAPTER_HEADING.match(heading):
        return heading, index + 1

    next_index = index + 1
    while next_index < len(lines):
        continuation = lines[next_index].strip()
        if (
            not continuation
            or continuation.startswith("[[Page ")
            or set(continuation) <= {"-", "=", "_"}
            or STRUCTURE_START.match(continuation)
            or PARAGRAPH_MARKER.match(continuation)
            or continuation.startswith(("Authority:", "Source:", "Editorial Note:"))
            or re.match(r"^\d+(?:[.\s-])", continuation)
        ):
            break
        if heading.endswith((".", "?", "!", "]")):
            break
        heading = f"{heading} {continuation}"
        next_index += 1

    return heading, next_index


def _paragraph_level(
    marker: str, paragraph_stack: list[tuple[int, dict[str, Any]]]
) -> int:
    """Infer CFR paragraph depth from its marker and surrounding paragraphs."""
    if marker.isdigit():
        return 2
    if marker.islower():
        if marker in {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}:
            if any(level == 2 for level, _ in paragraph_stack):
                return 3
        return 1
    if marker.isupper():
        if marker in {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}:
            if any(level == 4 for level, _ in paragraph_stack):
                return 5
        return 4
    return 1


def _append_text(node: dict[str, Any], text: str) -> None:
    """Append a source line to a hierarchy node's text."""
    if text:
        node["text"] = " ".join(filter(None, [node.get("text", ""), text]))


def extract_legal_hierarchy(file_path: str | Path) -> dict[str, Any]:
    """Parse CFR title-through-paragraph hierarchy from a text document."""
    source = Path(file_path)
    lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
    hierarchy: list[dict[str, Any]] = []
    current: dict[str, dict[str, Any] | None] = {
        level: None
        for level in ("title", "chapter", "subchapter", "part", "subpart", "section")
    }
    paragraph_stack: list[tuple[int, dict[str, Any]]] = []

    def reset_below(level: str) -> None:
        levels = ["title", "chapter", "subchapter", "part", "subpart", "section"]
        for child_level in levels[levels.index(level) + 1 :]:
            current[child_level] = None
        paragraph_stack.clear()

    def children_of(*levels: str) -> list[dict[str, Any]]:
        for level in levels:
            parent = current[level]
            if parent is not None:
                return parent["children"]
        return hierarchy

    index = 0
    while index < len(lines):
        raw_line = lines[index].strip()
        heading, next_index = _logical_heading(lines, index)

        match = TITLE_HEADING.match(heading)
        if match:
            number = match.group("number")
            node = _heading_node(
                "title", number, match.group("title"), heading, f"{number} CFR"
            )
            current["title"] = _find_or_add(hierarchy, node)
            reset_below("title")
            index = next_index
            continue

        match = CHAPTER_HEADING.match(heading)
        if match:
            number = match.group("number")
            title_number = (current["title"] or {}).get("number", "")
            citation = f"{title_number} CFR Chapter {number}".strip()
            node = _heading_node(
                "chapter", number, match.group("title"), heading, citation
            )
            current["chapter"] = _find_or_add(
                children_of("title"), node
            )
            reset_below("chapter")
            index = next_index
            continue

        match = SUBCHAPTER_HEADING.match(heading)
        if match:
            number = match.group("number")
            title_number = (current["title"] or {}).get("number", "")
            citation = f"{title_number} CFR Subchapter {number}".strip()
            node = _heading_node(
                "subchapter", number, match.group("title"), heading, citation
            )
            current["subchapter"] = _find_or_add(
                children_of("chapter", "title"), node
            )
            reset_below("subchapter")
            index = next_index
            continue

        match = PART_HEADING.match(heading)
        if match:
            number = match.group("number")
            title_number = (current["title"] or {}).get("number", "")
            citation = f"{title_number} CFR Part {number}".strip()
            node = _heading_node("part", number, match.group("title"), heading, citation)
            current["part"] = _find_or_add(
                children_of("subchapter", "chapter", "title"), node
            )
            reset_below("part")
            index = next_index
            continue

        match = SUBPART_HEADING.match(heading)
        if match and current["part"] is not None:
            number = match.group("number").upper()
            title_number = (current["title"] or {}).get("number", "")
            part_number = current["part"]["number"]
            citation = f"{title_number} CFR Part {part_number}, Subpart {number}".strip()
            node = _heading_node(
                "subpart", number, match.group("title"), heading, citation
            )
            current["subpart"] = _find_or_add(children_of("part"), node)
            reset_below("subpart")
            index = next_index
            continue

        match = SECTION_HEADING.match(heading)
        if match and current["part"] is not None:
            number = match.group("number")
            title_number = (current["title"] or {}).get("number", "")
            citation = f"{title_number} CFR {number}".strip()
            node = _heading_node(
                "section", number, match.group("title"), heading, citation
            )
            node["text"] = ""
            current["section"] = _find_or_add(
                children_of("subpart", "part"), node
            )
            reset_below("section")
            index = next_index
            continue

        section = current["section"]
        paragraph_match = PARAGRAPH_MARKER.match(raw_line)
        if section is not None and paragraph_match:
            marker = paragraph_match.group("marker")
            level = _paragraph_level(marker, paragraph_stack)
            while paragraph_stack and paragraph_stack[-1][0] >= level:
                paragraph_stack.pop()

            parent = paragraph_stack[-1][1] if paragraph_stack else section
            parent_path = parent.get("path", "")
            path = f"{parent_path}({marker})"
            paragraph = {
                "type": "paragraph",
                "marker": marker,
                "path": path,
                "citation": f"{section['citation']}{path}",
                "text": paragraph_match.group("text").strip(),
                "children": [],
            }
            parent["children"].append(paragraph)
            paragraph_stack.append((level, paragraph))
        elif (
            section is not None
            and raw_line
            and not raw_line.startswith("[[Page ")
            and not set(raw_line) <= {"-", "=", "_"}
        ):
            target = paragraph_stack[-1][1] if paragraph_stack else section
            _append_text(target, raw_line)

        index += 1

    return {
        "source_document": source.name,
        "document_title": extract_title(source),
        "hierarchy": hierarchy,
    }


def write_chapter_metadata(
    file_path: str | Path, output_dir: str | Path
) -> Path:
    """Write chapter metadata to a document-specific JSON file."""
    source = Path(file_path)
    document_dir = Path(output_dir) / source.stem
    document_dir.mkdir(parents=True, exist_ok=True)

    chapters = []
    for heading in extract_chapter_titles(source):
        match = CHAPTER_HEADING.match(heading)
        if match:
            chapters.append(
                {
                    "chapter_number": match.group("number"),
                    "title": match.group("title"),
                    "heading": heading,
                }
            )

    metadata = {
        "source_document": source.name,
        "chapter_count": len(chapters),
        "chapters": chapters,
    }
    metadata_path = document_dir / "chapters.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata_path


def write_legal_hierarchy(
    file_path: str | Path, output_dir: str | Path
) -> Path:
    """Write the complete legal hierarchy to a document-specific JSON file."""
    source = Path(file_path)
    document_dir = Path(output_dir) / source.stem
    document_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = document_dir / "hierarchy.json"
    metadata_path.write_text(
        json.dumps(extract_legal_hierarchy(source), indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata_path


def create_document_records(data_dir: str | Path) -> list[dict[str, Any]]:
    """Return one RAG metadata record for each document under data_dir."""
    root = Path(data_dir)
    if not root.is_dir():
        raise ValueError(f"Data directory does not exist: {root}")

    records = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith(".") or path.suffix == ".json":
            continue

        relative_path = path.relative_to(root).as_posix()
        document_id = hashlib.sha256(relative_path.encode()).hexdigest()[:16]

        records.append(
            {
                "document_id": document_id,
                "title": extract_title(path),
                "source_file_name": path.name,
                "source_format": path.suffix.lstrip(".").lower() or "unknown",
                "raw_storage_uri": relative_path,
            }
        )

    return records


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Extract CFR legal hierarchy.")
    parser.add_argument("document", help="Regulation document to process")
    parser.add_argument(
        "--output-dir",
        default="metadata",
        help="Root directory for generated metadata (default: metadata)",
    )
    args = parser.parse_args()

    output_path = write_legal_hierarchy(args.document, args.output_dir)
    sys.stdout.write(f"Wrote {output_path}\n")
