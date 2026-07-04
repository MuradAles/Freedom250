"""Build a validated dense-vector index from legal RAG chunks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol, Sequence

import numpy as np


DEFAULT_MODEL = "Snowflake/snowflake-arctic-embed-l-v2.0"


class Embedder(Protocol):
    """Interface used by the index builder."""

    model_name: str

    def encode(
        self,
        texts: Sequence[str],
        batch_size: int,
        max_length: int,
    ) -> np.ndarray:
        """Return one dense vector for each input text."""


class ArcticEmbedder:
    """Local Snowflake Arctic dense embedding adapter."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        use_fp16: bool = False,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "sentence-transformers is required. Install dependencies with "
                "`python3 -m pip install -r requirements-embedding.txt`."
            ) from error

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        if use_fp16:
            self._model.half()

    def encode(
        self,
        texts: Sequence[str],
        batch_size: int,
        max_length: int,
    ) -> np.ndarray:
        self._model.max_seq_length = max_length
        return np.asarray(
            self._model.encode(
                list(texts),
                batch_size=batch_size,
                convert_to_numpy=True,
                normalize_embeddings=False,
                show_progress_bar=True,
            ),
            dtype=np.float32,
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_chunks(chunks_path: str | Path) -> list[dict[str, Any]]:
    """Load and validate chunk records from JSONL."""
    path = Path(chunks_path)
    chunks = []
    chunk_ids = set()

    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}"
                ) from error

            chunk_id = chunk.get("chunk_id")
            embedding_text = chunk.get("embedding_text")
            if not isinstance(chunk_id, str) or not chunk_id:
                raise ValueError(f"Chunk on line {line_number} has no chunk_id")
            if chunk_id in chunk_ids:
                raise ValueError(f"Duplicate chunk_id: {chunk_id}")
            if not isinstance(embedding_text, str) or not embedding_text.strip():
                raise ValueError(
                    f"Chunk {chunk_id} has no embedding_text"
                )

            chunk_ids.add(chunk_id)
            chunks.append(chunk)

    if not chunks:
        raise ValueError(f"No chunks found in {path}")
    return chunks


def normalize_vectors(vectors: np.ndarray) -> np.ndarray:
    """Validate and L2-normalize dense vectors for cosine retrieval."""
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2:
        raise ValueError("Embeddings must be a two-dimensional matrix")
    if not np.isfinite(matrix).all():
        raise ValueError("Embeddings contain NaN or infinite values")

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("Embeddings contain a zero vector")
    return matrix / norms


def _index_record(row: int, chunk: dict[str, Any]) -> dict[str, Any]:
    """Create the metadata record corresponding to one vector row."""
    return {
        "row": row,
        "chunk_id": chunk["chunk_id"],
        "source_document": chunk.get("source_document"),
        "citation": chunk.get("citation"),
        "text": chunk.get("text"),
        "word_count": chunk.get("word_count"),
        "metadata": chunk.get("metadata", {}),
    }


def build_embedding_index(
    chunks_path: str | Path,
    output_dir: str | Path | None = None,
    embedder: Embedder | None = None,
    model_name: str = DEFAULT_MODEL,
    batch_size: int = 8,
    max_length: int = 1024,
    use_fp16: bool = False,
) -> dict[str, Path]:
    """Embed chunks and overwrite a validated local NumPy index."""
    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")
    if max_length < 1:
        raise ValueError("max_length must be greater than zero")

    chunks_file = Path(chunks_path)
    chunks = load_chunks(chunks_file)
    destination = Path(output_dir) if output_dir else chunks_file.parent
    destination.mkdir(parents=True, exist_ok=True)

    active_embedder = embedder or ArcticEmbedder(
        model_name=model_name,
        use_fp16=use_fp16,
    )
    vectors = np.asarray(
        active_embedder.encode(
            [chunk["embedding_text"] for chunk in chunks],
            batch_size=batch_size,
            max_length=max_length,
        ),
        dtype=np.float32,
    )
    if vectors.ndim != 2:
        raise ValueError("Embeddings must be a two-dimensional matrix")
    if vectors.shape[0] != len(chunks):
        raise ValueError(
            f"Embedding row count {vectors.shape[0]} does not match "
            f"chunk count {len(chunks)}"
        )
    vectors = normalize_vectors(vectors)

    vectors_path = destination / "embeddings.npy"
    index_path = destination / "embedding_index.jsonl"
    manifest_path = destination / "embedding_manifest.json"

    np.save(vectors_path, vectors, allow_pickle=False)
    index_path.write_text(
        "".join(
            json.dumps(_index_record(row, chunk)) + "\n"
            for row, chunk in enumerate(chunks)
        ),
        encoding="utf-8",
    )

    manifest = {
        "model": active_embedder.model_name,
        "model_provider": active_embedder.model_name.split("/", 1)[0],
        "vector_count": len(chunks),
        "dimensions": int(vectors.shape[1]),
        "dtype": str(vectors.dtype),
        "normalized": True,
        "distance": "cosine",
        "batch_size": batch_size,
        "max_length": max_length,
        "chunks_file": chunks_file.name,
        "chunks_sha256": _sha256(chunks_file),
        "embeddings_sha256": _sha256(vectors_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "vectors": vectors_path,
        "index": index_path,
        "manifest": manifest_path,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Embed legal RAG chunks with Snowflake Arctic Embed."
    )
    parser.add_argument("chunks", help="Path to chunks.jsonl")
    parser.add_argument(
        "--output-dir",
        help="Output directory (default: directory containing chunks.jsonl)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument(
        "--fp16",
        action="store_true",
        help="Enable half precision on supported accelerators",
    )
    args = parser.parse_args()

    paths = build_embedding_index(
        args.chunks,
        output_dir=args.output_dir,
        model_name=args.model,
        batch_size=args.batch_size,
        max_length=args.max_length,
        use_fp16=args.fp16,
    )
    for name, path in paths.items():
        print(f"{name}: {path}")
