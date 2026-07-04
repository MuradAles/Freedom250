"""Download current SBA regulations from the official eCFR API."""

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
from typing import Any, Callable, Sequence


ECFR_BASE_URL = "https://www.ecfr.gov"
TITLES_URL = f"{ECFR_BASE_URL}/api/versioner/v1/titles.json"
VERSIONS_URL = f"{ECFR_BASE_URL}/api/versioner/v1/versions/title-13.json"
DEFAULT_OUTPUT_DIR = Path("data/sba-regulations")
LEDGER_FILENAME = "download-ledger.json"
ARTIFACT_FILENAMES = (
    "title-13-chapter-i.xml",
    "title-13-chapter-i.txt",
    "structure.json",
    "manifest.json",
)
USER_AGENT = "fwa-legal-rag/1.0 (eCFR data downloader)"
STRUCTURAL_TAGS = {
    "DIV1",
    "DIV2",
    "DIV3",
    "DIV4",
    "DIV5",
    "DIV6",
    "DIV7",
    "DIV8",
    "DIV9",
}


@dataclass(frozen=True)
class DownloadOutcome:
    """Result of an update check or regulation download."""

    status: str
    effective_date: str
    paths: dict[str, Path]


def _request_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return _normalize_text("".join(element.itertext()))


def latest_title_date(
    fetch: Callable[[str], bytes] = _request_bytes,
) -> str:
    """Return the latest eCFR date available for Title 13."""
    titles = json.loads(fetch(TITLES_URL))
    for title in titles.get("titles", []):
        if title.get("number") == 13:
            date = title.get("up_to_date_as_of")
            if not date:
                raise ValueError("Title 13 has no current eCFR date")
            return str(date)
    raise ValueError("Title 13 was not found in the eCFR titles response")


def latest_sba_change_date(
    fetch: Callable[[str], bytes] = _request_bytes,
) -> str:
    """Return the latest eCFR issue date affecting SBA Parts 1 through 199."""
    versions = json.loads(fetch(VERSIONS_URL)).get("content_versions", [])
    dates = []
    for version in versions:
        match = re.match(r"\d+", str(version.get("part", "")))
        if match and 1 <= int(match.group()) <= 199:
            issue_date = version.get("issue_date")
            if issue_date:
                dates.append(str(issue_date))
    if not dates:
        raise ValueError("No SBA regulation versions were found for Title 13")
    return max(dates)


def _chapter_node(root: ET.Element) -> tuple[ET.Element, ET.Element]:
    title = next(
        (
            node
            for node in root.iter("DIV1")
            if node.get("TYPE") == "TITLE" and node.get("N") == "13"
        ),
        None,
    )
    if title is None:
        raise ValueError("The eCFR response does not contain Title 13")

    chapter = next(
        (
            node
            for node in title.findall("DIV3")
            if node.get("TYPE") == "CHAPTER" and node.get("N") == "I"
        ),
        None,
    )
    if chapter is None:
        raise ValueError("The eCFR response does not contain Chapter I")
    if "SMALL BUSINESS ADMINISTRATION" not in _element_text(chapter.find("HEAD")).upper():
        raise ValueError("Title 13 Chapter I is not labeled as SBA regulations")
    return title, chapter


def extract_sba_xml(title_xml: bytes) -> bytes:
    """Extract Title 13 Chapter I while retaining eCFR document metadata."""
    root = ET.fromstring(title_xml)
    title, chapter = _chapter_node(root)

    extracted_root = ET.Element(root.tag, root.attrib)
    for child in root:
        if child is title:
            title_copy = ET.Element(title.tag, title.attrib)
            head = title.find("HEAD")
            if head is not None:
                title_copy.append(copy.deepcopy(head))
            title_copy.append(copy.deepcopy(chapter))
            extracted_root.append(title_copy)
        elif child.tag in {"AMDDATE", "VOLUME"}:
            extracted_root.append(copy.deepcopy(child))

    ET.indent(extracted_root, space="  ")
    declaration = b'<?xml version="1.0" encoding="UTF-8"?>\n'
    return declaration + ET.tostring(extracted_root, encoding="utf-8") + b"\n"


def extract_sba_structure(title_structure: dict[str, Any]) -> dict[str, Any]:
    """Extract the SBA chapter from the official Title 13 structure."""
    if str(title_structure.get("identifier")) != "13":
        raise ValueError("The structure response is not for Title 13")

    chapter = next(
        (
            child
            for child in title_structure.get("children", [])
            if child.get("type") == "chapter"
            and str(child.get("identifier")) == "I"
        ),
        None,
    )
    if chapter is None:
        raise ValueError("The Title 13 structure has no Chapter I")
    if "Small Business Administration" not in chapter.get("label", ""):
        raise ValueError("Title 13 Chapter I is not labeled as SBA regulations")

    result = dict(title_structure)
    result["children"] = [chapter]
    return result


def _heading(element: ET.Element) -> str:
    node_type = element.get("TYPE")
    number = element.get("N", "")
    source_heading = _element_text(element.find("HEAD"))

    if node_type == "TITLE":
        title = re.sub(
            rf"^Title\s+{re.escape(number)}\s*[—-]*\s*",
            "",
            source_heading,
        )
        return f"TITLE {number}--{title.upper()}"
    if node_type == "CHAPTER":
        title = re.sub(
            rf"^CHAPTER\s+{re.escape(number)}\s*[—-]*\s*",
            "",
            source_heading,
        )
        return f"CHAPTER {number}--{title.upper()}"
    if node_type == "PART":
        title = re.sub(
            rf"^PARTS?\s+{re.escape(number)}\s*[—-]*\s*",
            "",
            source_heading,
        )
        return f"PART {number}--{title.upper()}"
    if node_type == "SUBPART":
        title = re.sub(
            rf"^Subpart\s+{re.escape(number)}\s*[—-]*\s*",
            "",
            source_heading,
        )
        return f"Subpart {number}--{title}"
    if node_type == "SECTION":
        normalized_number = number.replace("§", "").replace(" ", "")
        title = re.sub(
            rf"^(?:(?:§+|Sec\.)\s*)?{re.escape(number)}\s*",
            "",
            source_heading,
        )
        return f"Sec. {normalized_number} {title}"
    if node_type == "APPENDIX":
        return source_heading or number
    return source_heading


def _text_lines(element: ET.Element) -> list[str]:
    lines = []
    heading = _heading(element)
    if heading:
        lines.append(heading)

    for child in element:
        if child.tag == "HEAD" or child.tag in STRUCTURAL_TAGS:
            continue
        text = _element_text(child)
        if text:
            lines.append(text)

    for child in element:
        if child.tag in STRUCTURAL_TAGS:
            lines.extend(_text_lines(child))
    return lines


def sba_xml_to_text(sba_xml: bytes) -> str:
    """Convert extracted eCFR XML to the text format used by the parser."""
    root = ET.fromstring(sba_xml)
    title, _ = _chapter_node(root)
    return "\n\n".join(_text_lines(title)).strip() + "\n"


def _count_nodes(root: ET.Element, node_type: str) -> int:
    return sum(
        1
        for node in root.iter()
        if node.tag in STRUCTURAL_TAGS and node.get("TYPE") == node_type
    )


def _artifact_paths(directory: Path) -> dict[str, Path]:
    return {
        "xml": directory / "title-13-chapter-i.xml",
        "text": directory / "title-13-chapter-i.txt",
        "structure": directory / "structure.json",
        "manifest": directory / "manifest.json",
        "ledger": directory / LEDGER_FILENAME,
    }


def _file_records(directory: Path) -> dict[str, dict[str, Any]]:
    records = {}
    for filename in ARTIFACT_FILENAMES:
        path = directory / filename
        content = path.read_bytes()
        records[filename] = {
            "bytes": len(content),
            "sha256": _sha256(content),
        }
    return records


def _files_match(directory: Path, records: dict[str, Any]) -> bool:
    for filename in ARTIFACT_FILENAMES:
        expected = records.get(filename)
        path = directory / filename
        if not expected or not path.is_file():
            return False
        content = path.read_bytes()
        if len(content) != expected.get("bytes"):
            return False
        if _sha256(content) != expected.get("sha256"):
            return False
    return True


def _copy_artifacts(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for filename in ARTIFACT_FILENAMES:
        shutil.copy2(source / filename, destination / filename)


def _load_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": 1,
            "title": 13,
            "chapter": "I",
            "agency": "Small Business Administration",
            "downloads": [],
            "checks": [],
        }
    ledger = json.loads(path.read_text(encoding="utf-8"))
    if ledger.get("schema_version") != 1:
        raise ValueError(f"Unsupported download ledger schema: {path}")
    return ledger


def _write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    ledger["checks"] = ledger.get("checks", [])[-200:]
    path.write_bytes(_json_bytes(ledger))


def _record_check(
    ledger: dict[str, Any],
    status: str,
    effective_date: str,
    latest_sba_change: str,
) -> None:
    checked_at = datetime.now(timezone.utc).isoformat()
    ledger["last_checked_at"] = checked_at
    ledger["current_date"] = effective_date
    ledger["latest_sba_change"] = latest_sba_change
    ledger.setdefault("checks", []).append(
        {
            "checked_at": checked_at,
            "status": status,
            "effective_date": effective_date,
            "latest_sba_change": latest_sba_change,
        }
    )


def _find_cached_download(
    ledger: dict[str, Any],
    destination: Path,
    effective_date: str | None = None,
    latest_sba_change: str | None = None,
) -> tuple[dict[str, Any], Path] | None:
    for download in reversed(ledger.get("downloads", [])):
        if effective_date and download.get("effective_date") != effective_date:
            continue
        if (
            latest_sba_change
            and download.get("latest_sba_change") != latest_sba_change
        ):
            continue
        archive = destination / download.get("archive", "")
        if _files_match(archive, download.get("files", {})):
            return download, archive
    return None


def _adopt_existing_download(
    ledger: dict[str, Any],
    destination: Path,
    latest_sba_change: str,
) -> tuple[dict[str, Any], Path] | None:
    paths = _artifact_paths(destination)
    if not paths["manifest"].is_file():
        return None

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    effective_date = manifest.get("up_to_date_as_of")
    manifest_files = manifest.get("files", {})
    for filename in ARTIFACT_FILENAMES[:-1]:
        expected = manifest_files.get(filename)
        path = destination / filename
        if not expected or not path.is_file():
            return None
        content = path.read_bytes()
        if (
            len(content) != expected.get("bytes")
            or _sha256(content) != expected.get("sha256")
        ):
            return None
    if not effective_date:
        return None
    existing_change = manifest.get("latest_sba_change")
    if existing_change and existing_change != latest_sba_change:
        return None
    if not existing_change and effective_date < latest_sba_change:
        return None

    archive = destination / "versions" / effective_date
    _copy_artifacts(destination, archive)
    download = {
        "effective_date": effective_date,
        "latest_sba_change": latest_sba_change,
        "downloaded_at": manifest.get("downloaded_at"),
        "archive": str(archive.relative_to(destination)),
        "source_urls": manifest.get("source_urls", {}),
        "files": _file_records(archive),
        "counts": manifest.get("counts", {}),
        "adopted_from_existing_files": True,
    }
    ledger["downloads"] = [
        item
        for item in ledger.get("downloads", [])
        if item.get("effective_date") != effective_date
    ]
    ledger.setdefault("downloads", []).append(download)
    return download, archive


def download_sba_regulations(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    date: str | None = None,
    fetch: Callable[[str], bytes] = _request_bytes,
    force: bool = False,
) -> DownloadOutcome:
    """Update the SBA corpus only when eCFR or local integrity requires it."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths = _artifact_paths(destination)
    ledger = _load_ledger(paths["ledger"])

    if date:
        effective_date = date
        latest_sba_change = date
        cached = _find_cached_download(
            ledger,
            destination,
            effective_date=effective_date,
        )
    else:
        effective_date = latest_title_date(fetch)
        latest_sba_change = latest_sba_change_date(fetch)
        cached = _find_cached_download(
            ledger,
            destination,
            latest_sba_change=latest_sba_change,
        )
        if cached is None and not force:
            cached = _adopt_existing_download(
                ledger,
                destination,
                latest_sba_change,
            )

    if cached and not force:
        download, archive = cached
        _copy_artifacts(archive, destination)
        effective_date = download["effective_date"]
        _record_check(
            ledger,
            "skipped_unchanged",
            effective_date,
            latest_sba_change,
        )
        _write_ledger(paths["ledger"], ledger)
        return DownloadOutcome(
            "skipped_unchanged",
            effective_date,
            paths,
        )

    full_url = (
        f"{ECFR_BASE_URL}/api/versioner/v1/full/"
        f"{effective_date}/title-13.xml"
    )
    structure_url = (
        f"{ECFR_BASE_URL}/api/versioner/v1/structure/"
        f"{effective_date}/title-13.json"
    )

    title_xml = fetch(full_url)
    title_structure = json.loads(fetch(structure_url))
    sba_xml = extract_sba_xml(title_xml)
    sba_structure = extract_sba_structure(title_structure)
    sba_text = sba_xml_to_text(sba_xml).encode("utf-8")
    root = ET.fromstring(sba_xml)

    archive = destination / "versions" / effective_date
    archive.mkdir(parents=True, exist_ok=True)
    archive_paths = _artifact_paths(archive)

    structure_bytes = _json_bytes(sba_structure)
    downloaded_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "title": 13,
        "chapter": "I",
        "agency": "Small Business Administration",
        "up_to_date_as_of": effective_date,
        "latest_sba_change": latest_sba_change,
        "downloaded_at": downloaded_at,
        "source": "Electronic Code of Federal Regulations",
        "source_urls": {
            "full_title_xml": full_url,
            "title_structure": structure_url,
        },
        "files": {
            archive_paths["xml"].name: {
                "bytes": len(sba_xml),
                "sha256": _sha256(sba_xml),
            },
            archive_paths["text"].name: {
                "bytes": len(sba_text),
                "sha256": _sha256(sba_text),
            },
            archive_paths["structure"].name: {
                "bytes": len(structure_bytes),
                "sha256": _sha256(structure_bytes),
            },
        },
        "counts": {
            "parts": _count_nodes(root, "PART"),
            "subparts": _count_nodes(root, "SUBPART"),
            "sections": _count_nodes(root, "SECTION"),
            "appendices": _count_nodes(root, "APPENDIX"),
        },
    }

    archive_paths["xml"].write_bytes(sba_xml)
    archive_paths["text"].write_bytes(sba_text)
    archive_paths["structure"].write_bytes(structure_bytes)
    archive_paths["manifest"].write_bytes(_json_bytes(manifest))
    _copy_artifacts(archive, destination)

    previous = _find_cached_download(
        ledger,
        destination,
        effective_date=effective_date,
    )
    status = "downloaded"
    if force:
        status = "redownloaded_forced"
    elif previous:
        status = "redownloaded_repair"
    elif ledger.get("downloads"):
        status = "downloaded_update"

    download = {
        "effective_date": effective_date,
        "latest_sba_change": latest_sba_change,
        "downloaded_at": downloaded_at,
        "archive": str(archive.relative_to(destination)),
        "source_urls": manifest["source_urls"],
        "files": _file_records(archive),
        "counts": manifest["counts"],
    }
    ledger["downloads"] = [
        item
        for item in ledger.get("downloads", [])
        if item.get("effective_date") != effective_date
    ]
    ledger["downloads"].append(download)
    _record_check(ledger, status, effective_date, latest_sba_change)
    _write_ledger(paths["ledger"], ledger)
    return DownloadOutcome(status, effective_date, paths)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download current SBA regulations from the official eCFR API."
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--date",
        help="Optional eCFR point-in-time date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload even when a verified cached version exists",
    )
    args = parser.parse_args(argv)

    outcome = download_sba_regulations(
        args.output_dir,
        date=args.date,
        force=args.force,
    )
    print(f"status: {outcome.status}")
    print(f"effective_date: {outcome.effective_date}")
    for name, path in outcome.paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
