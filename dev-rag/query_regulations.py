"""Search regulation chunks stored in Qdrant from the command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any, Sequence

if __package__:
    from src.build_embeddings import (
        DEFAULT_MODEL,
        ArcticEmbedder,
        Embedder,
        normalize_vectors,
    )
    from src.qdrant_store import DEFAULT_QDRANT_URL
else:
    from build_embeddings import (
        DEFAULT_MODEL,
        ArcticEmbedder,
        Embedder,
        normalize_vectors,
    )
    from qdrant_store import DEFAULT_QDRANT_URL


DEFAULT_COLLECTION = "fwa_relevant-regulations"


@dataclass(frozen=True)
class SearchResult:
    """One regulation chunk returned by vector search."""

    score: float
    chunk_id: str | None
    citation: str | None
    source: str | None
    regulation: str | None
    text: str


def search_regulations(
    query: str,
    client: Any,
    models: Any,
    embedder: Embedder,
    collection_name: str = DEFAULT_COLLECTION,
    limit: int = 5,
    source: str | None = None,
    score_threshold: float | None = None,
    max_length: int = 1024,
) -> list[SearchResult]:
    """Embed a question and retrieve the nearest regulation chunks."""
    question = query.strip()
    if not question:
        raise ValueError("Query must not be empty")
    if limit < 1:
        raise ValueError("Limit must be greater than zero")

    query_vector = normalize_vectors(
        embedder.encode([question], batch_size=1, max_length=max_length)
    )[0]
    query_filter = None
    if source:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.corpus_source",
                    match=models.MatchValue(value=source),
                )
            ]
        )

    response = client.query_points(
        collection_name=collection_name,
        query=query_vector.tolist(),
        query_filter=query_filter,
        limit=limit,
        score_threshold=score_threshold,
        with_payload=True,
        with_vectors=False,
    )

    results = []
    for point in response.points:
        payload = point.payload or {}
        metadata = payload.get("metadata") or {}
        results.append(
            SearchResult(
                score=float(point.score),
                chunk_id=payload.get("chunk_id"),
                citation=payload.get("citation"),
                source=metadata.get("corpus_source"),
                regulation=metadata.get("regulation_label"),
                text=payload.get("text") or "",
            )
        )
    return results


def format_results(results: Sequence[SearchResult]) -> str:
    """Render search results for terminal output."""
    if not results:
        return "No matching regulation chunks found."

    sections = []
    for rank, result in enumerate(results, start=1):
        heading = result.citation or "Citation unavailable"
        details = [f"score: {result.score:.4f}"]
        if result.source:
            details.append(f"source: {result.source}")
        if result.regulation:
            details.append(f"regulation: {result.regulation}")
        sections.append(
            "\n".join(
                [
                    f"{rank}. {heading}",
                    f"   {' | '.join(details)}",
                    f"   {result.text.strip()}",
                ]
            )
        )
    return "\n\n".join(sections)


def _connect(url: str, api_key: str | None) -> tuple[Any, Any]:
    try:
        from qdrant_client import QdrantClient, models
    except ImportError as error:
        raise RuntimeError(
            "qdrant-client is required. Install dependencies with "
            "`python3 -m pip install -r requirements-vector-store.txt`."
        ) from error
    return QdrantClient(url=url, api_key=api_key), models


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Search the regulation vector collection in Qdrant."
    )
    parser.add_argument(
        "query",
        nargs="+",
        help="Question or regulation text to search for",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--source", help="Only return one corpus source slug")
    parser.add_argument("--score-threshold", type=float)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument(
        "--url",
        default=os.environ.get("QDRANT_URL", DEFAULT_QDRANT_URL),
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("QDRANT_API_KEY"),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON",
    )
    args = parser.parse_args(argv)

    try:
        client, models = _connect(args.url, args.api_key)
        embedder = ArcticEmbedder(model_name=args.model)
        results = search_regulations(
            " ".join(args.query),
            client,
            models,
            embedder,
            collection_name=args.collection,
            limit=args.limit,
            source=args.source,
            score_threshold=args.score_threshold,
            max_length=args.max_length,
        )
    except (RuntimeError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        print(format_results(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
