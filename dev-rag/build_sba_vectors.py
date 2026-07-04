"""Run the complete SBA regulation-to-vector artifact pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

if __package__:
    from src.build_embeddings import (
        DEFAULT_MODEL,
        Embedder,
        build_embedding_index,
        load_chunks,
    )
    from src.document_records import write_legal_hierarchy
    from src.download_sba_regulations import (
        DEFAULT_OUTPUT_DIR as DEFAULT_DATA_DIR,
        DownloadOutcome,
        download_sba_regulations,
    )
    from src.rag_chunks import write_rag_chunks
else:
    from build_embeddings import (
        DEFAULT_MODEL,
        Embedder,
        build_embedding_index,
        load_chunks,
    )
    from document_records import write_legal_hierarchy
    from download_sba_regulations import (
        DEFAULT_OUTPUT_DIR as DEFAULT_DATA_DIR,
        DownloadOutcome,
        download_sba_regulations,
    )
    from rag_chunks import write_rag_chunks


DEFAULT_METADATA_DIR = Path("metadata")
PIPELINE_MANIFEST = "vector_pipeline_manifest.json"


@dataclass(frozen=True)
class VectorPipelineOutcome:
    """Result and generated paths from one pipeline run."""

    status: str
    download_status: str
    source_date: str
    chunk_count: int
    paths: dict[str, Path]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _output_paths(source: Path, metadata_dir: Path) -> dict[str, Path]:
    document_dir = metadata_dir / source.stem
    return {
        "hierarchy": document_dir / "hierarchy.json",
        "chunks": document_dir / "chunks.jsonl",
        "vectors": document_dir / "embeddings.npy",
        "index": document_dir / "embedding_index.jsonl",
        "embedding_manifest": document_dir / "embedding_manifest.json",
        "pipeline_manifest": document_dir / PIPELINE_MANIFEST,
    }


def _artifacts_are_current(
    paths: dict[str, Path],
    source_sha256: str,
    max_words: int,
    model_name: str,
    max_length: int,
    use_fp16: bool,
) -> bool:
    manifest_path = paths["pipeline_manifest"]
    if not manifest_path.is_file():
        return False

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    expected = {
        "source_sha256": source_sha256,
        "max_words": max_words,
        "model": model_name,
        "max_length": max_length,
        "use_fp16": use_fp16,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        return False

    for name in (
        "hierarchy",
        "chunks",
        "vectors",
        "index",
        "embedding_manifest",
    ):
        path = paths[name]
        record = manifest.get("artifacts", {}).get(path.name)
        if not path.is_file() or not record:
            return False
        if path.stat().st_size != record.get("bytes"):
            return False
        if _sha256(path) != record.get("sha256"):
            return False
    return True


def _pipeline_manifest(
    paths: dict[str, Path],
    source: Path,
    source_date: str,
    max_words: int,
    model_name: str,
    batch_size: int,
    max_length: int,
    use_fp16: bool,
    chunk_count: int,
) -> dict[str, Any]:
    artifacts = {}
    for name in (
        "hierarchy",
        "chunks",
        "vectors",
        "index",
        "embedding_manifest",
    ):
        path = paths[name]
        artifacts[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }

    return {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_document": source.name,
        "source_date": source_date,
        "source_sha256": _sha256(source),
        "max_words": max_words,
        "model": model_name,
        "batch_size": batch_size,
        "max_length": max_length,
        "use_fp16": use_fp16,
        "chunk_count": chunk_count,
        "artifacts": artifacts,
        "qdrant_ready": True,
    }


def build_sba_vector_artifacts(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    metadata_dir: str | Path = DEFAULT_METADATA_DIR,
    max_words: int = 350,
    embedder: Embedder | None = None,
    model_name: str = DEFAULT_MODEL,
    batch_size: int = 8,
    max_length: int = 1024,
    use_fp16: bool = False,
    force_download: bool = False,
    force_build: bool = False,
    skip_download: bool = False,
    downloader: Callable[..., DownloadOutcome] = download_sba_regulations,
) -> VectorPipelineOutcome:
    """Download, parse, chunk, and embed all current SBA regulations."""
    if max_words < 1:
        raise ValueError("max_words must be greater than zero")

    corpus_dir = Path(data_dir)
    source = corpus_dir / "title-13-chapter-i.txt"
    source_manifest_path = corpus_dir / "manifest.json"

    if skip_download:
        if not source.is_file() or not source_manifest_path.is_file():
            raise FileNotFoundError(
                "SBA corpus is missing; run without --skip-download first"
            )
        download_status = "skipped_by_request"
    else:
        download = downloader(corpus_dir, force=force_download)
        download_status = download.status

    if not source.is_file():
        raise FileNotFoundError(f"Missing SBA regulation text: {source}")

    source_manifest = json.loads(
        source_manifest_path.read_text(encoding="utf-8")
    )
    source_date = str(source_manifest["up_to_date_as_of"])
    active_model_name = embedder.model_name if embedder else model_name
    paths = _output_paths(source, Path(metadata_dir))
    source_sha256 = _sha256(source)

    if not force_build and _artifacts_are_current(
        paths,
        source_sha256,
        max_words,
        active_model_name,
        max_length,
        use_fp16,
    ):
        manifest = json.loads(
            paths["pipeline_manifest"].read_text(encoding="utf-8")
        )
        return VectorPipelineOutcome(
            "skipped_unchanged",
            download_status,
            source_date,
            int(manifest["chunk_count"]),
            paths,
        )

    hierarchy_path = write_legal_hierarchy(source, metadata_dir)
    chunks_path = write_rag_chunks(
        hierarchy_path,
        max_words=max_words,
    )
    embedding_paths = build_embedding_index(
        chunks_path,
        output_dir=chunks_path.parent,
        embedder=embedder,
        model_name=model_name,
        batch_size=batch_size,
        max_length=max_length,
        use_fp16=use_fp16,
    )

    paths.update(
        {
            "hierarchy": hierarchy_path,
            "chunks": chunks_path,
            "vectors": embedding_paths["vectors"],
            "index": embedding_paths["index"],
            "embedding_manifest": embedding_paths["manifest"],
        }
    )
    chunks = load_chunks(chunks_path)
    manifest = _pipeline_manifest(
        paths,
        source,
        source_date,
        max_words,
        active_model_name,
        batch_size,
        max_length,
        use_fp16,
        len(chunks),
    )
    paths["pipeline_manifest"].write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    status = "rebuilt_forced" if force_build else "built"
    return VectorPipelineOutcome(
        status,
        download_status,
        source_date,
        len(chunks),
        paths,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download, parse, chunk, and vectorize all current SBA regulations."
        )
    )
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--metadata-dir", default=DEFAULT_METADATA_DIR)
    parser.add_argument("--max-words", type=int, default=350)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Redownload the SBA corpus even when the ledger is current",
    )
    parser.add_argument(
        "--force-build",
        action="store_true",
        help="Rebuild hierarchy, chunks, and vectors even when unchanged",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Use the existing local SBA corpus without checking eCFR",
    )
    args = parser.parse_args(argv)

    outcome = build_sba_vector_artifacts(
        data_dir=args.data_dir,
        metadata_dir=args.metadata_dir,
        max_words=args.max_words,
        model_name=args.model,
        batch_size=args.batch_size,
        max_length=args.max_length,
        use_fp16=args.fp16,
        force_download=args.force_download,
        force_build=args.force_build,
        skip_download=args.skip_download,
    )
    print(f"status: {outcome.status}")
    print(f"download_status: {outcome.download_status}")
    print(f"source_date: {outcome.source_date}")
    print(f"chunk_count: {outcome.chunk_count}")
    for name, path in outcome.paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
