"""Print the legal RAG pipeline as a terminal-friendly diagram."""

from __future__ import annotations


PIPELINE_DIAGRAM = r"""
+---------------------------+
| Relevant Regulations      |
| CFR Titles 2, 13, and 48  |
+-------------+-------------+
              |
              v
+---------------------------+
| Parsing                   |
| Build the legal hierarchy |
+-------------+-------------+
              |
              v
+---------------------------+
| Chunking                  |
| Create searchable passages|
+-------------+-------------+
              |
              v
+---------------------------+
| Vectorization             |
| Convert chunks to vectors |
+-------------+-------------+
              |
              v
+---------------------------+
| Qdrant                    |
| Store vectors and metadata|
+-------------+-------------+
              |
              v
+---------------------------+
| Retrieval                 |
| Find relevant chunks      |
+-------------+-------------+
              |
              v
+---------------------------+
| Context and Reranking     |
| Expand and order evidence |
+-------------+-------------+
              |
              v
+---------------------------+
| LLM Answer                |
| Evaluate with citations   |
+---------------------------+
""".strip()


def main() -> int:
    print(PIPELINE_DIAGRAM)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
