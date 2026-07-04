"""Download a curated, update-aware corpus of SBA-relevant regulations."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

if __package__:
    from src.download_sba_regulations import (
        ECFR_BASE_URL,
        TITLES_URL,
        _text_lines,
    )
    from src.regulation_sources import (
        CORE_REGULATION_SOURCES,
        RegulationSource,
    )
else:
    from download_sba_regulations import ECFR_BASE_URL, TITLES_URL, _text_lines
    from regulation_sources import CORE_REGULATION_SOURCES, RegulationSource


DEFAULT_OUTPUT_DIR = Path("data/relevant-regulations")
LEDGER_FILENAME = "download-ledger.json"
CORPUS_MANIFEST = "corpus-manifest.json"
USER_AGENT = "fwa-legal-rag/1.0 (relevant eCFR corpus downloader)"
ARTIFACT_NAMES = ("xml", "text", "structure", "manifest")


@dataclass(frozen=True)
class CorpusDownloadOutcome:
    """Result of updating the configured regulation corpus."""

    status: str
    source_statuses: dict[str, str]
    manifest_path: Path
    document_paths: tuple[Path, ...]


def _request_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _corpus_fingerprint(documents: list[dict[str, Any]]) -> str:
    stable_documents = [
        {
            "slug": document["slug"],
            "cfr_scope": document["cfr_scope"],
            "effective_date": document["effective_date"],
            "latest_source_change": document["latest_source_change"],
            "text_sha256": document["text_sha256"],
        }
        for document in documents
    ]
    content = json.dumps(
        stable_documents,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(content)


def _source_paths(directory: Path, source: RegulationSource) -> dict[str, Path]:
    return {
        "xml": directory / f"{source.slug}.xml",
        "text": directory / f"{source.slug}.txt",
        "structure": directory / "structure.json",
        "manifest": directory / "manifest.json",
    }


def _file_records(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    return {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": _sha256_path(path),
        }
        for name, path in paths.items()
        if name in ARTIFACT_NAMES
    }


def _files_match(
    paths: dict[str, Path],
    records: dict[str, Any],
) -> bool:
    for name in ARTIFACT_NAMES:
        path = paths[name]
        expected = records.get(path.name)
        if not path.is_file() or not expected:
            return False
        if path.stat().st_size != expected.get("bytes"):
            return False
        if _sha256_path(path) != expected.get("sha256"):
            return False
    return True


def _copy_artifacts(
    source_paths: dict[str, Path],
    destination_paths: dict[str, Path],
) -> None:
    destination_paths["xml"].parent.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_NAMES:
        shutil.copy2(source_paths[name], destination_paths[name])


def _title_dates(titles: dict[str, Any]) -> dict[int, str]:
    return {
        int(item["number"]): str(item["up_to_date_as_of"])
        for item in titles.get("titles", [])
        if item.get("number") and item.get("up_to_date_as_of")
    }


def _version_matches(
    version: dict[str, Any],
    source: RegulationSource,
) -> bool:
    part = str(version.get("part", ""))
    if source.version_parts and part not in source.version_parts:
        return False
    if source.version_part_range:
        match = re.match(r"\d+", part)
        if not match:
            return False
        lower, upper = source.version_part_range
        if not lower <= int(match.group()) <= upper:
            return False
    if source.section_prefix:
        return str(version.get("identifier", "")).startswith(
            source.section_prefix
        )
    return bool(source.version_parts or source.version_part_range)


def latest_source_change(
    versions: dict[str, Any],
    source: RegulationSource,
    fallback_date: str,
) -> str:
    """Return the latest issue date affecting one configured source."""
    dates = [
        str(version["issue_date"])
        for version in versions.get("content_versions", [])
        if version.get("issue_date") and _version_matches(version, source)
    ]
    return max(dates) if dates else fallback_date


def latest_structure_change(
    structure: dict[str, Any],
    fallback_date: str,
) -> str:
    """Return the latest received date represented in extracted structure."""
    dates = []

    def visit(node: dict[str, Any]) -> None:
        received_on = node.get("received_on")
        if received_on:
            dates.append(str(received_on)[:10])
        for child in node.get("children", []):
            visit(child)

    visit(structure)
    return max(dates) if dates else fallback_date


def _matches_xml(node: ET.Element, node_type: str, identifier: str) -> bool:
    return (
        node.get("TYPE", "").lower() == node_type.lower()
        and node.get("N") == identifier
    )


def _find_xml_descendant(
    node: ET.Element,
    node_type: str,
    identifier: str,
) -> ET.Element | None:
    return next(
        (
            candidate
            for candidate in node.iter()
            if candidate is not node
            and _matches_xml(candidate, node_type, identifier)
        ),
        None,
    )


def _xml_path(
    title: ET.Element,
    source: RegulationSource,
) -> list[ET.Element]:
    current = title
    matched = []
    for node_type, identifier in source.path:
        current = _find_xml_descendant(current, node_type, identifier)
        if current is None:
            raise ValueError(
                f"Could not find {node_type} {identifier} for {source.slug}"
            )
        matched.append(current)
    return matched


def _prune_xml_sections(
    node: ET.Element,
    prefix: str,
) -> bool:
    contains_selected = (
        node.get("TYPE") == "SECTION"
        and node.get("N", "").startswith(prefix)
    )
    for child in list(node):
        if child.get("TYPE") == "SECTION":
            if not child.get("N", "").startswith(prefix):
                node.remove(child)
            else:
                contains_selected = True
        elif child.tag.startswith("DIV"):
            if not _prune_xml_sections(child, prefix):
                node.remove(child)
            else:
                contains_selected = True
    return contains_selected


def extract_source_xml(
    title_xml: bytes,
    source: RegulationSource,
) -> bytes:
    """Extract one configured hierarchy path from a full eCFR title."""
    root = ET.fromstring(title_xml)
    title = next(
        (
            node
            for node in root.iter()
            if _matches_xml(node, "title", str(source.title))
        ),
        None,
    )
    if title is None:
        raise ValueError(f"Title {source.title} was not found")

    matched = _xml_path(title, source)
    target = copy.deepcopy(matched[-1])
    if source.section_prefix and not _prune_xml_sections(
        target,
        source.section_prefix,
    ):
        raise ValueError(f"No sections matched {source.section_prefix}")

    for ancestor in reversed(matched[:-1]):
        parent = ET.Element(ancestor.tag, ancestor.attrib)
        head = ancestor.find("HEAD")
        if head is not None:
            parent.append(copy.deepcopy(head))
        parent.append(target)
        target = parent

    title_copy = ET.Element(title.tag, title.attrib)
    title_head = title.find("HEAD")
    if title_head is not None:
        title_copy.append(copy.deepcopy(title_head))
    title_copy.append(target)

    extracted_root = ET.Element(root.tag, root.attrib)
    for child in root:
        if child.tag in {"AMDDATE", "VOLUME"}:
            extracted_root.append(copy.deepcopy(child))
    extracted_root.append(title_copy)
    ET.indent(extracted_root, space="  ")
    declaration = b'<?xml version="1.0" encoding="UTF-8"?>\n'
    return declaration + ET.tostring(extracted_root, encoding="utf-8") + b"\n"


def _find_structure_descendant(
    node: dict[str, Any],
    node_type: str,
    identifier: str,
) -> dict[str, Any] | None:
    for child in node.get("children", []):
        if (
            child.get("type", "").lower() == node_type.lower()
            and str(child.get("identifier")) == identifier
        ):
            return child
        match = _find_structure_descendant(child, node_type, identifier)
        if match:
            return match
    return None


def _prune_structure_sections(
    node: dict[str, Any],
    prefix: str,
) -> bool:
    selected_children = []
    contains_selected = False
    for child in node.get("children", []):
        if child.get("type") == "section":
            if str(child.get("identifier", "")).startswith(prefix):
                selected_children.append(child)
                contains_selected = True
        elif _prune_structure_sections(child, prefix):
            selected_children.append(child)
            contains_selected = True
    node["children"] = selected_children
    return contains_selected


def extract_source_structure(
    title_structure: dict[str, Any],
    source: RegulationSource,
) -> dict[str, Any]:
    """Extract one configured hierarchy path from eCFR structure JSON."""
    if str(title_structure.get("identifier")) != str(source.title):
        raise ValueError(f"Structure is not for Title {source.title}")

    current = title_structure
    matched = []
    for node_type, identifier in source.path:
        current = _find_structure_descendant(current, node_type, identifier)
        if current is None:
            raise ValueError(
                f"Could not find {node_type} {identifier} for {source.slug}"
            )
        matched.append(copy.deepcopy(current))

    target = matched[-1]
    if source.section_prefix and not _prune_structure_sections(
        target,
        source.section_prefix,
    ):
        raise ValueError(f"No structure sections matched {source.section_prefix}")

    for ancestor in reversed(matched[:-1]):
        ancestor["children"] = [target]
        target = ancestor
    result = dict(title_structure)
    result["children"] = [target]
    return result


def extracted_xml_to_text(content: bytes) -> str:
    """Convert an extracted eCFR document to parser-friendly text."""
    root = ET.fromstring(content)
    title = next(
        (
            node
            for node in root.iter()
            if node.get("TYPE") == "TITLE"
        ),
        None,
    )
    if title is None:
        raise ValueError("Extracted XML has no title")
    return "\n\n".join(_text_lines(title)).strip() + "\n"


def _counts(root: ET.Element) -> dict[str, int]:
    labels = {
        "PART": "parts",
        "SUBPART": "subparts",
        "SECTION": "sections",
        "APPENDIX": "appendices",
    }
    return {
        label: sum(
            1 for node in root.iter() if node.get("TYPE") == name
        )
        for name, label in labels.items()
    }


def _load_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "sources": {}, "checks": []}
    ledger = json.loads(path.read_text(encoding="utf-8"))
    if ledger.get("schema_version") != 1:
        raise ValueError(f"Unsupported corpus ledger schema: {path}")
    return ledger


def _cached_paths(
    destination: Path,
    source: RegulationSource,
    entry: dict[str, Any],
) -> dict[str, Path]:
    return _source_paths(destination / entry["archive"], source)


def _write_source(
    destination: Path,
    source: RegulationSource,
    effective_date: str,
    latest_change: str,
    title_xml: bytes,
    title_structure: dict[str, Any],
) -> tuple[dict[str, Path], dict[str, Any]]:
    extracted_xml = extract_source_xml(title_xml, source)
    extracted_structure = extract_source_structure(title_structure, source)
    extracted_text = extracted_xml_to_text(extracted_xml).encode("utf-8")
    root = ET.fromstring(extracted_xml)

    source_dir = destination / "sources" / source.slug
    archive_dir = source_dir / "versions" / effective_date
    archive_paths = _source_paths(archive_dir, source)
    archive_dir.mkdir(parents=True, exist_ok=True)

    downloaded_at = datetime.now(timezone.utc).isoformat()
    full_url = (
        f"{ECFR_BASE_URL}/api/versioner/v1/full/"
        f"{effective_date}/title-{source.title}.xml"
    )
    structure_url = (
        f"{ECFR_BASE_URL}/api/versioner/v1/structure/"
        f"{effective_date}/title-{source.title}.json"
    )
    structure_bytes = _json_bytes(extracted_structure)
    manifest = {
        "slug": source.slug,
        "title": source.title,
        "label": source.label,
        "cfr_scope": source.cfr_scope,
        "rationale": source.rationale,
        "up_to_date_as_of": effective_date,
        "latest_source_change": latest_change,
        "downloaded_at": downloaded_at,
        "source": "Electronic Code of Federal Regulations",
        "source_urls": {
            "full_title_xml": full_url,
            "title_structure": structure_url,
        },
        "counts": _counts(root),
    }

    archive_paths["xml"].write_bytes(extracted_xml)
    archive_paths["text"].write_bytes(extracted_text)
    archive_paths["structure"].write_bytes(structure_bytes)
    archive_paths["manifest"].write_bytes(_json_bytes(manifest))
    current_paths = _source_paths(source_dir, source)
    _copy_artifacts(archive_paths, current_paths)

    entry = {
        "effective_date": effective_date,
        "latest_source_change": latest_change,
        "structure_sha256": _sha256_bytes(structure_bytes),
        "downloaded_at": downloaded_at,
        "archive": str(archive_dir.relative_to(destination)),
        "files": _file_records(archive_paths),
        "counts": manifest["counts"],
    }
    return current_paths, entry


def download_relevant_regulations(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    sources: Iterable[RegulationSource] = CORE_REGULATION_SOURCES,
    date: str | None = None,
    fetch: Callable[[str], bytes] = _request_bytes,
    force: bool = False,
) -> CorpusDownloadOutcome:
    """Update every configured regulation without redownloading unchanged data."""
    configured = tuple(sources)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    ledger_path = destination / LEDGER_FILENAME
    ledger = _load_ledger(ledger_path)

    titles = json.loads(fetch(TITLES_URL))
    title_dates = _title_dates(titles)
    full_xml_by_title: dict[tuple[int, str], bytes] = {}
    structure_by_title: dict[tuple[int, str], dict[str, Any]] = {}
    statuses = {}
    documents = []

    for source in configured:
        effective_date = date or title_dates[source.title]
        key = (source.title, effective_date)
        structure_url = (
            f"{ECFR_BASE_URL}/api/versioner/v1/structure/"
            f"{effective_date}/title-{source.title}.json"
        )
        if key not in structure_by_title:
            structure_by_title[key] = json.loads(fetch(structure_url))
        source_structure = extract_source_structure(
            structure_by_title[key],
            source,
        )
        structure_sha256 = _sha256_bytes(_json_bytes(source_structure))
        latest_change = latest_structure_change(
            source_structure,
            effective_date,
        )

        entry = ledger.get("sources", {}).get(source.slug)
        current_paths = _source_paths(
            destination / "sources" / source.slug,
            source,
        )
        cached = None
        if entry and entry.get("structure_sha256") == structure_sha256:
            archive_paths = _cached_paths(destination, source, entry)
            if _files_match(archive_paths, entry.get("files", {})):
                cached = archive_paths

        if cached and not force:
            _copy_artifacts(cached, current_paths)
            status = "skipped_unchanged"
        else:
            full_url = (
                f"{ECFR_BASE_URL}/api/versioner/v1/full/"
                f"{effective_date}/title-{source.title}.xml"
            )
            if key not in full_xml_by_title:
                full_xml_by_title[key] = fetch(full_url)
            current_paths, new_entry = _write_source(
                destination,
                source,
                effective_date,
                latest_change,
                full_xml_by_title[key],
                structure_by_title[key],
            )
            ledger.setdefault("sources", {})[source.slug] = new_entry
            status = "redownloaded_forced" if force else "downloaded"
            if entry and not force:
                status = "downloaded_update"

        statuses[source.slug] = status
        manifest = json.loads(
            current_paths["manifest"].read_text(encoding="utf-8")
        )
        documents.append(
            {
                "slug": source.slug,
                "title": source.title,
                "label": source.label,
                "cfr_scope": source.cfr_scope,
                "rationale": source.rationale,
                "effective_date": manifest["up_to_date_as_of"],
                "latest_source_change": manifest["latest_source_change"],
                "text_path": str(
                    current_paths["text"].relative_to(destination)
                ),
                "manifest_path": str(
                    current_paths["manifest"].relative_to(destination)
                ),
                "status": status,
                "counts": manifest["counts"],
                "text_sha256": _sha256_path(current_paths["text"]),
            }
        )

    checked_at = datetime.now(timezone.utc).isoformat()
    ledger["checks"] = [
        *ledger.get("checks", [])[-199:],
        {"checked_at": checked_at, "source_statuses": statuses},
    ]
    ledger["last_checked_at"] = checked_at
    ledger_path.write_bytes(_json_bytes(ledger))

    corpus_manifest = {
        "schema_version": 1,
        "generated_at": checked_at,
        "source_count": len(documents),
        "corpus_fingerprint": _corpus_fingerprint(documents),
        "documents": documents,
    }
    manifest_path = destination / CORPUS_MANIFEST
    manifest_path.write_bytes(_json_bytes(corpus_manifest))
    overall = (
        "skipped_unchanged"
        if all(status == "skipped_unchanged" for status in statuses.values())
        else "updated"
    )
    return CorpusDownloadOutcome(
        overall,
        statuses,
        manifest_path,
        tuple(
            destination / document["text_path"] for document in documents
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download the curated SBA-relevant federal regulation corpus."
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--date", help="Optional point-in-time eCFR date")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    outcome = download_relevant_regulations(
        args.output_dir,
        date=args.date,
        force=args.force,
    )
    print(f"status: {outcome.status}")
    print(f"manifest: {outcome.manifest_path}")
    for slug, status in outcome.source_statuses.items():
        print(f"{slug}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
