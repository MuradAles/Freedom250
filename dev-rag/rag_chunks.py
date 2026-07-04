"""Create retrieval chunks from parsed CFR hierarchy metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterator


SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
CONTEXT_TYPES = ("title", "chapter", "subchapter", "part", "subpart")


def _word_count(text: str) -> int:
    return len(text.split())


def _paragraph_text(node: dict[str, Any]) -> str:
    """Render a paragraph and all nested paragraphs as readable text."""
    lines = []
    marker = node.get("marker")
    text = node.get("text", "").strip()
    if marker:
        lines.append(f"({marker}) {text}".strip())
    elif text:
        lines.append(text)

    lines.extend(
        child_text
        for child in node.get("children", [])
        if (child_text := _paragraph_text(child))
    )
    return "\n".join(lines)


def _section_text(section: dict[str, Any]) -> str:
    """Render the complete text belonging to a section."""
    parts = []
    if section.get("text", "").strip():
        parts.append(section["text"].strip())
    parts.extend(
        paragraph_text
        for child in section.get("children", [])
        if (paragraph_text := _paragraph_text(child))
    )
    return "\n".join(parts)


def _split_sentences(text: str, max_words: int) -> list[str]:
    """Group sentences without exceeding max_words when possible."""
    sentences = SENTENCE_END.split(text.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for sentence in sentences:
        sentence_words = sentence.split()
        if current and current_words + len(sentence_words) > max_words:
            chunks.append(" ".join(current))
            current = []
            current_words = 0

        while len(sentence_words) > max_words:
            if current:
                chunks.append(" ".join(current))
                current = []
                current_words = 0
            chunks.append(" ".join(sentence_words[:max_words]))
            sentence_words = sentence_words[max_words:]

        if sentence_words:
            current.append(" ".join(sentence_words))
            current_words += len(sentence_words)

    if current:
        chunks.append(" ".join(current))
    return chunks


def _context_lines(ancestors: list[dict[str, Any]]) -> list[str]:
    """Render legal ancestry for embedding context."""
    return [
        f"{node['type'].title()} {node['number']} - {node['title']}"
        for node in ancestors
        if node["type"] in CONTEXT_TYPES
    ]


def _chunk_id(
    source_document: str,
    citation: str,
    part: int,
    text: str,
) -> str:
    value = f"{source_document}::{citation}::{part}::{text}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _make_chunk(
    source_document: str,
    ancestors: list[dict[str, Any]],
    section: dict[str, Any],
    citation: str,
    paragraph_path: str,
    text: str,
    part: int = 1,
) -> dict[str, Any]:
    context = _context_lines(ancestors)
    context.append(f"Section {section['number']} - {section['title']}")
    if paragraph_path and section.get("text", "").strip():
        context.append(f"Section context: {section['text'].strip()}")
    context.append(f"Citation: {citation}")
    embedding_text = "\n".join([*context, "", text]).strip()

    metadata = {
        f"{node['type']}_number": node["number"]
        for node in ancestors
        if node["type"] in CONTEXT_TYPES
    }
    metadata.update(
        {
            "section_number": section["number"],
            "section_title": section["title"],
            "paragraph_path": paragraph_path,
        }
    )

    return {
        "chunk_id": _chunk_id(source_document, citation, part, text),
        "source_document": source_document,
        "citation": citation,
        "text": text,
        "embedding_text": embedding_text,
        "word_count": _word_count(text),
        "chunk_part": part,
        "metadata": metadata,
    }


def _split_paragraph(
    source_document: str,
    ancestors: list[dict[str, Any]],
    section: dict[str, Any],
    paragraph: dict[str, Any],
    max_words: int,
    inherited_text: str = "",
) -> list[dict[str, Any]]:
    """Split one paragraph at nested paragraph boundaries when necessary."""
    paragraph_text = _paragraph_text(paragraph)
    text = "\n".join(
        value for value in (inherited_text, paragraph_text) if value
    )
    if _word_count(text) <= max_words:
        return [
            _make_chunk(
                source_document,
                ancestors,
                section,
                paragraph["citation"],
                paragraph["path"],
                text,
            )
        ]

    children = paragraph.get("children", [])
    if children:
        own_text = f"({paragraph['marker']}) {paragraph.get('text', '')}".strip()
        parent_context = "\n".join(
            value for value in (inherited_text, own_text) if value
        )
        chunks = []
        for child in children:
            chunks.extend(
                _split_paragraph(
                    source_document,
                    ancestors,
                    section,
                    child,
                    max_words,
                    inherited_text=parent_context,
                )
            )
        if chunks:
            return chunks

    return [
        _make_chunk(
            source_document,
            ancestors,
            section,
            paragraph["citation"],
            paragraph["path"],
            text_part,
            part=part,
        )
        for part, text_part in enumerate(
            _split_sentences(text, max_words),
            start=1,
        )
    ]


def _section_chunks(
    source_document: str,
    ancestors: list[dict[str, Any]],
    section: dict[str, Any],
    max_words: int,
) -> list[dict[str, Any]]:
    """Keep a short section whole or split a long section structurally."""
    complete_text = _section_text(section)
    if not complete_text:
        return []

    if _word_count(complete_text) <= max_words or not section.get("children"):
        text_parts = _split_sentences(complete_text, max_words)
        return [
            _make_chunk(
                source_document,
                ancestors,
                section,
                section["citation"],
                "",
                text,
                part=part,
            )
            for part, text in enumerate(text_parts, start=1)
        ]

    chunks = []
    introduction = section.get("text", "").strip()
    if introduction:
        chunks.append(
            _make_chunk(
                source_document,
                ancestors,
                section,
                section["citation"],
                "",
                introduction,
            )
        )

    for paragraph in section["children"]:
        chunks.extend(
            _split_paragraph(
                source_document,
                ancestors,
                section,
                paragraph,
                max_words,
            )
        )
    return chunks


def _iter_sections(
    nodes: list[dict[str, Any]],
    ancestors: list[dict[str, Any]] | None = None,
) -> Iterator[tuple[list[dict[str, Any]], dict[str, Any]]]:
    """Yield every section with its structural ancestors."""
    ancestors = ancestors or []
    for node in nodes:
        if node["type"] == "section":
            yield ancestors, node
            continue
        yield from _iter_sections(node.get("children", []), [*ancestors, node])


def create_rag_chunks(
    hierarchy_metadata: dict[str, Any],
    max_words: int = 350,
) -> list[dict[str, Any]]:
    """Create structure-aware chunks suitable for embedding and retrieval."""
    if max_words < 1:
        raise ValueError("max_words must be greater than zero")

    source_document = hierarchy_metadata["source_document"]
    chunks = []
    for ancestors, section in _iter_sections(hierarchy_metadata["hierarchy"]):
        chunks.extend(
            _section_chunks(
                source_document,
                ancestors,
                section,
                max_words,
            )
        )

    seen_ids: dict[str, int] = {}
    for chunk in chunks:
        base_id = chunk["chunk_id"]
        occurrence = seen_ids.get(base_id, 0) + 1
        seen_ids[base_id] = occurrence
        if occurrence > 1:
            value = f"{base_id}::{occurrence}"
            chunk["chunk_id"] = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]

    return chunks


def write_rag_chunks(
    hierarchy_path: str | Path,
    output_path: str | Path | None = None,
    max_words: int = 350,
) -> Path:
    """Create chunks from hierarchy JSON and overwrite a JSONL output file."""
    hierarchy_file = Path(hierarchy_path)
    metadata = json.loads(hierarchy_file.read_text(encoding="utf-8"))
    chunks = create_rag_chunks(metadata, max_words=max_words)

    destination = (
        Path(output_path)
        if output_path is not None
        else hierarchy_file.with_name("chunks.jsonl")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(json.dumps(chunk) + "\n" for chunk in chunks),
        encoding="utf-8",
    )
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create legal RAG chunks.")
    parser.add_argument("hierarchy", help="Path to hierarchy.json")
    parser.add_argument(
        "--output",
        help="Output JSONL path (default: chunks.jsonl beside hierarchy.json)",
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=350,
        help="Maximum words per chunk (default: 350)",
    )
    args = parser.parse_args()

    path = write_rag_chunks(args.hierarchy, args.output, args.max_words)
    print(f"Wrote {path}")
