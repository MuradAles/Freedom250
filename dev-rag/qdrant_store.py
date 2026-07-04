"""Load generated embedding artifacts into a Qdrant collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np


DEFAULT_QDRANT_URL = "http://localhost:6333"
DEFAULT_COLLECTION_PREFIX = "fwa"
POINT_NAMESPACE = uuid.UUID("ee42e5cb-76af-4c6e-a429-d29b58dc6342")


@dataclass(frozen=True)
class EmbeddingArtifacts:
    """Validated vectors and their corresponding chunk payloads."""

    vectors: np.ndarray
    records: list[dict[str, Any]]
    manifest: dict[str, Any]

    @property
    def dimensions(self) -> int:
        return int(self.vectors.shape[1])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_index(path: Path) -> list[dict[str, Any]]:
    records = []
    chunk_ids = set()
    rows = set()

    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Invalid JSON on line {line_number} of {path}"
                ) from error

            row = record.get("row")
            chunk_id = record.get("chunk_id")
            if not isinstance(row, int) or row < 0:
                raise ValueError(f"Index line {line_number} has an invalid row")
            if row in rows:
                raise ValueError(f"Duplicate embedding row: {row}")
            if not isinstance(chunk_id, str) or not chunk_id:
                raise ValueError(f"Index line {line_number} has no chunk_id")
            if chunk_id in chunk_ids:
                raise ValueError(f"Duplicate chunk_id: {chunk_id}")

            rows.add(row)
            chunk_ids.add(chunk_id)
            records.append(record)

    records.sort(key=lambda record: record["row"])
    if [record["row"] for record in records] != list(range(len(records))):
        raise ValueError("Embedding index rows must be contiguous from zero")
    return records


def load_embedding_artifacts(
    artifacts_dir: str | Path,
) -> EmbeddingArtifacts:
    """Load and cross-check the NumPy, JSONL, and manifest artifacts."""
    directory = Path(artifacts_dir)
    vectors_path = directory / "embeddings.npy"
    index_path = directory / "embedding_index.jsonl"
    manifest_path = directory / "embedding_manifest.json"

    for path in (vectors_path, index_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(f"Missing embedding artifact: {path}")

    vectors = np.load(vectors_path, allow_pickle=False, mmap_mode="r")
    if vectors.ndim != 2:
        raise ValueError("Embeddings must be a two-dimensional matrix")
    if not np.issubdtype(vectors.dtype, np.floating):
        raise ValueError("Embeddings must use a floating-point dtype")
    if not np.isfinite(vectors).all():
        raise ValueError("Embeddings contain NaN or infinite values")

    records = _load_index(index_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    expected_count = manifest.get("vector_count")
    expected_dimensions = manifest.get("dimensions")
    if vectors.shape[0] != len(records):
        raise ValueError("Vector and embedding index counts do not match")
    if expected_count != vectors.shape[0]:
        raise ValueError("Manifest vector_count does not match embeddings")
    if expected_dimensions != vectors.shape[1]:
        raise ValueError("Manifest dimensions do not match embeddings")
    if manifest.get("distance") != "cosine":
        raise ValueError("Qdrant ingestion requires cosine embeddings")
    if not manifest.get("normalized"):
        raise ValueError("Qdrant ingestion requires normalized embeddings")

    expected_hash = manifest.get("embeddings_sha256")
    if expected_hash and expected_hash != _sha256(vectors_path):
        raise ValueError("Embedding file hash does not match the manifest")

    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, rtol=1e-4, atol=1e-6):
        raise ValueError("Embeddings are not L2-normalized")

    return EmbeddingArtifacts(vectors, records, manifest)


def default_collection_name(artifacts_dir: str | Path) -> str:
    """Create a stable Qdrant collection name from the document directory."""
    document_name = Path(artifacts_dir).resolve().name
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", document_name).strip("_")
    return f"{DEFAULT_COLLECTION_PREFIX}_{normalized or 'legal_chunks'}"


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, chunk_id))


def _batches(items: Sequence[Any], batch_size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def ingest_embeddings(
    client: Any,
    models: Any,
    artifacts: EmbeddingArtifacts,
    collection_name: str,
    batch_size: int = 64,
    recreate: bool = False,
) -> int:
    """Create the collection when needed and upsert every vector and payload."""
    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")

    collection_exists = client.collection_exists(collection_name)
    if collection_exists and recreate:
        client.delete_collection(collection_name)
        collection_exists = False

    if not collection_exists:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=artifacts.dimensions,
                distance=models.Distance.COSINE,
            ),
        )

    model_name = artifacts.manifest.get("model")
    for record_batch in _batches(artifacts.records, batch_size):
        points = []
        for record in record_batch:
            row = record["row"]
            payload = dict(record)
            payload["embedding_model"] = model_name
            points.append(
                models.PointStruct(
                    id=_point_id(record["chunk_id"]),
                    vector=artifacts.vectors[row].tolist(),
                    payload=payload,
                )
            )
        client.upsert(
            collection_name=collection_name,
            points=points,
            wait=True,
        )

    return len(artifacts.records)


def transfer_embedding_artifacts(
    artifacts_dir: str | Path,
    collection_name: str | None = None,
    url: str = DEFAULT_QDRANT_URL,
    api_key: str | None = None,
    batch_size: int = 64,
    recreate: bool = False,
) -> tuple[str, int]:
    """Connect to Qdrant and transfer one document's embedding artifacts."""
    try:
        from qdrant_client import QdrantClient, models
    except ImportError as error:
        raise RuntimeError(
            "qdrant-client is required. Install dependencies with "
            "`python3 -m pip install -r requirements-vector-store.txt`."
        ) from error

    artifacts = load_embedding_artifacts(artifacts_dir)
    active_collection = collection_name or default_collection_name(artifacts_dir)
    client = QdrantClient(url=url, api_key=api_key)
    count = ingest_embeddings(
        client,
        models,
        artifacts,
        active_collection,
        batch_size=batch_size,
        recreate=recreate,
    )
    return active_collection, count


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Transfer generated legal chunk embeddings into Qdrant."
    )
    parser.add_argument(
        "artifacts_dir",
        help="Directory containing embeddings.npy and its index and manifest",
    )
    parser.add_argument(
        "--collection",
        help="Collection name (default: derived from the artifact directory)",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("QDRANT_URL", DEFAULT_QDRANT_URL),
        help=f"Qdrant URL (default: {DEFAULT_QDRANT_URL})",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("QDRANT_API_KEY"),
        help="Qdrant API key (or set QDRANT_API_KEY)",
    )
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete and recreate the collection before ingestion",
    )
    args = parser.parse_args(argv)

    collection, count = transfer_embedding_artifacts(
        args.artifacts_dir,
        collection_name=args.collection,
        url=args.url,
        api_key=args.api_key,
        batch_size=args.batch_size,
        recreate=args.recreate,
    )
    print(f"Transferred {count} vectors to Qdrant collection: {collection}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
