"""Build one Qdrant-ready vector index from all configured regulations."""

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
    from src.download_relevant_regulations import (
        DEFAULT_OUTPUT_DIR as DEFAULT_DATA_DIR,
        CorpusDownloadOutcome,
        download_relevant_regulations,
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
    from download_relevant_regulations import (
        DEFAULT_OUTPUT_DIR as DEFAULT_DATA_DIR,
        CorpusDownloadOutcome,
        download_relevant_regulations,
    )
    from rag_chunks import write_rag_chunks


DEFAULT_OUTPUT_DIR = Path("metadata/relevant-regulations")
PIPELINE_MANIFEST = "vector_pipeline_manifest.json"


@dataclass(frozen=True)
class RegulationVectorOutcome:
    """Result of building the combined regulation vector index."""

    status: str
    download_status: str
    source_count: int
    chunk_count: int
    paths: dict[str, Path]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _paths(output_dir: Path) -> dict[str, Path]:
    return {
        "chunks": output_dir / "chunks.jsonl",
        "vectors": output_dir / "embeddings.npy",
        "index": output_dir / "embedding_index.jsonl",
        "embedding_manifest": output_dir / "embedding_manifest.json",
        "pipeline_manifest": output_dir / PIPELINE_MANIFEST,
    }


def _corpus_fingerprint(corpus: dict[str, Any]) -> str:
    fingerprint = corpus.get("corpus_fingerprint")
    if fingerprint:
        return str(fingerprint)
    stable_documents = [
        {
            key: document.get(key)
            for key in (
                "slug",
                "cfr_scope",
                "effective_date",
                "latest_source_change",
                "text_sha256",
                "text_path",
            )
        }
        for document in corpus["documents"]
    ]
    content = json.dumps(
        stable_documents,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _current(
    paths: dict[str, Path],
    corpus_fingerprint: str,
    max_words: int,
    model_name: str,
    max_length: int,
    use_fp16: bool,
) -> bool:
    pipeline_path = paths["pipeline_manifest"]
    if not pipeline_path.is_file():
        return False
    try:
        manifest = json.loads(pipeline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = {
        "corpus_fingerprint": corpus_fingerprint,
        "max_words": max_words,
        "model": model_name,
        "max_length": max_length,
        "use_fp16": use_fp16,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        return False
    for name in ("chunks", "vectors", "index", "embedding_manifest"):
        path = paths[name]
        record = manifest.get("artifacts", {}).get(path.name)
        if (
            not path.is_file()
            or not record
            or path.stat().st_size != record.get("bytes")
            or _sha256(path) != record.get("sha256")
        ):
            return False
    return True


def _annotated_chunks(
    chunks_path: Path,
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    chunks = load_chunks(chunks_path)
    for chunk in chunks:
        chunk["corpus_source"] = document["slug"]
        chunk["regulation_label"] = document["label"]
        chunk["cfr_scope"] = document["cfr_scope"]
        metadata = chunk.setdefault("metadata", {})
        metadata.update(
            {
                "corpus_source": document["slug"],
                "regulation_label": document["label"],
                "cfr_scope": document["cfr_scope"],
                "source_rationale": document["rationale"],
                "effective_date": document["effective_date"],
            }
        )
    return chunks


def build_regulation_vector_artifacts(
    data_dir: str | Path = DEFAULT_DATA_DIR,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    max_words: int = 350,
    embedder: Embedder | None = None,
    model_name: str = DEFAULT_MODEL,
    batch_size: int = 8,
    max_length: int = 1024,
    use_fp16: bool = False,
    force_download: bool = False,
    force_build: bool = False,
    skip_download: bool = False,
    downloader: Callable[..., CorpusDownloadOutcome] = (
        download_relevant_regulations
    ),
) -> RegulationVectorOutcome:
    """Download, parse, chunk, and embed the complete configured corpus."""
    corpus_dir = Path(data_dir)
    corpus_manifest_path = corpus_dir / "corpus-manifest.json"
    if skip_download:
        if not corpus_manifest_path.is_file():
            raise FileNotFoundError(
                "Regulation corpus is missing; run without --skip-download"
            )
        download_status = "skipped_by_request"
    else:
        download = downloader(corpus_dir, force=force_download)
        download_status = download.status

    corpus = json.loads(corpus_manifest_path.read_text(encoding="utf-8"))
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths = _paths(destination)
    active_model = embedder.model_name if embedder else model_name
    corpus_fingerprint = _corpus_fingerprint(corpus)

    if not force_build and _current(
        paths,
        corpus_fingerprint,
        max_words,
        active_model,
        max_length,
        use_fp16,
    ):
        manifest = json.loads(
            paths["pipeline_manifest"].read_text(encoding="utf-8")
        )
        return RegulationVectorOutcome(
            "skipped_unchanged",
            download_status,
            int(manifest["source_count"]),
            int(manifest["chunk_count"]),
            paths,
        )

    all_chunks = []
    documents_dir = destination / "documents"
    for document in corpus["documents"]:
        source = corpus_dir / document["text_path"]
        hierarchy_path = write_legal_hierarchy(source, documents_dir)
        chunks_path = write_rag_chunks(
            hierarchy_path,
            max_words=max_words,
        )
        all_chunks.extend(_annotated_chunks(chunks_path, document))

    paths["chunks"].write_text(
        "".join(json.dumps(chunk) + "\n" for chunk in all_chunks),
        encoding="utf-8",
    )
    embedding_paths = build_embedding_index(
        paths["chunks"],
        output_dir=destination,
        embedder=embedder,
        model_name=model_name,
        batch_size=batch_size,
        max_length=max_length,
        use_fp16=use_fp16,
    )
    paths.update(
        {
            "vectors": embedding_paths["vectors"],
            "index": embedding_paths["index"],
            "embedding_manifest": embedding_paths["manifest"],
        }
    )

    artifacts = {
        paths[name].name: {
            "bytes": paths[name].stat().st_size,
            "sha256": _sha256(paths[name]),
        }
        for name in ("chunks", "vectors", "index", "embedding_manifest")
    }
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "corpus_manifest": str(corpus_manifest_path),
        "corpus_fingerprint": corpus_fingerprint,
        "source_count": len(corpus["documents"]),
        "sources": [
            {
                "slug": document["slug"],
                "cfr_scope": document["cfr_scope"],
                "effective_date": document["effective_date"],
            }
            for document in corpus["documents"]
        ],
        "max_words": max_words,
        "model": active_model,
        "batch_size": batch_size,
        "max_length": max_length,
        "use_fp16": use_fp16,
        "chunk_count": len(all_chunks),
        "artifacts": artifacts,
        "qdrant_ready": True,
    }
    paths["pipeline_manifest"].write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return RegulationVectorOutcome(
        "rebuilt_forced" if force_build else "built",
        download_status,
        len(corpus["documents"]),
        len(all_chunks),
        paths,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a combined vector index for relevant SBA regulations."
    )
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-words", type=int, default=350)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args(argv)

    outcome = build_regulation_vector_artifacts(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
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
    print(f"source_count: {outcome.source_count}")
    print(f"chunk_count: {outcome.chunk_count}")
    for name, path in outcome.paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
