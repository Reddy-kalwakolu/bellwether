# bellwether/context/__main__.py
"""`python -m bellwether.context` — ingest, chunk, embed, and search the corpus.

Day 7 promised `--chunk`, `--embed` and `--engines all` and shipped none of them,
so its published numbers came from an ad-hoc script and are not reproducible by
anyone reading the repo. That is the gap this closes.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from bellwether.context.chunking.models import Chunk
from bellwether.context.chunking.report import compare_strategies, format_comparison
from bellwether.context.chunking.router import chunk_corpus
from bellwether.context.config import load_env_file, settings
from bellwether.context.embedders import REGISTRY, get_embedder
from bellwether.context.embedding_run import EmbeddingRun, embed_corpus, format_runs
from bellwether.context.pipeline import format_report, ingest
from bellwether.context.retrieval.bm25 import BM25Index
from bellwether.context.retrieval.rerank import HeuristicReranker, LLMReranker
from bellwether.context.retrieval.rerank.base import Reranker
from bellwether.context.retrieval.search import SearchConfig, SearchMode, SearchService
from bellwether.context.store import JsonlDocumentStore
from bellwether.context.vectors import COLLECTION, InMemoryVectorStore, QdrantVectorStore
from bellwether.llm import get_client


def _build_parser() -> argparse.ArgumentParser:
    """Every verb the context layer exposes."""
    parser = argparse.ArgumentParser(description="Ingest, chunk, embed and search the corpus.")
    parser.add_argument("--root", type=Path, default=settings.repo_root)
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing.")
    parser.add_argument("--chunk", action="store_true", help="Chunk and report the comparison.")
    parser.add_argument("--embed", metavar="ENGINE", help="Embed the corpus with one engine.")
    parser.add_argument("--engines", metavar="all", help="Embed with every available engine.")
    parser.add_argument("--rebuild", action="store_true", help="Drop the collection first.")
    parser.add_argument("--search", metavar="QUERY", help="Search the corpus.")
    parser.add_argument(
        "--mode",
        default=SearchMode.HYBRID.value,
        choices=[mode.value for mode in SearchMode],
        help="Which retrieval configuration to search with.",
    )
    parser.add_argument("--engine", default="hashing", help="Which engine's vectors to search.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--qdrant", default="http://localhost:6333")
    parser.add_argument("--in-memory", action="store_true", help="Skip Qdrant entirely.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run whichever verb was asked for."""
    args = _build_parser().parse_args(argv)
    load_env_file()

    root: Path = args.root
    corpus_path: Path = args.corpus or root / settings.corpus_path
    store = JsonlDocumentStore(corpus_path)

    if args.search:
        return _search(args, store)

    report = ingest(root, store)
    if not args.dry_run:
        store.flush()
    print(format_report(report))
    print("dry run: nothing written" if args.dry_run else f"corpus at: {corpus_path}")

    documents = store.documents()
    # Bound unconditionally. Computing it inside the `if` below and then using it in
    # the `--embed` branch is an UnboundLocalError waiting for the first person who
    # passes --embed without --chunk.
    chunks: list[Chunk] = []

    if args.chunk or args.embed or args.engines:
        chunks = chunk_corpus(documents)
        print(f"\nchunks: {len(chunks)} from {len(documents)} documents")
        print(format_comparison(compare_strategies(documents)))

    if args.embed or args.engines:
        _embed(args, chunks)

    return 0


def _vector_store(args: argparse.Namespace) -> InMemoryVectorStore | QdrantVectorStore:
    """Qdrant unless asked otherwise; the in-memory store is a real fallback."""
    if args.in_memory:
        return InMemoryVectorStore()
    return QdrantVectorStore(base_url=args.qdrant, collection=COLLECTION)


def _embed(args: argparse.Namespace, chunks: list[Chunk]) -> None:
    """Embed with one engine or with every available one, and print the bill."""
    vectors = _vector_store(args)
    if args.rebuild and isinstance(vectors, QdrantVectorStore):
        vectors.drop_collection()

    names = list(REGISTRY) if args.engines == "all" else [args.embed]
    embedders = [get_embedder(name) for name in names if name]
    vectors.ensure_collection([embedder.spec for embedder in embedders])

    runs: list[EmbeddingRun] = []
    for embedder in embedders:
        available, reason = embedder.available()
        if not available:
            print(f"skipping {embedder.spec.name}: {reason}")
            continue
        runs.append(embed_corpus(chunks, embedder, vectors))

    if runs:
        print("\n" + format_runs(runs))


def _search(args: argparse.Namespace, store: JsonlDocumentStore) -> int:
    """Answer one query and print the hits with their provenance."""
    chunks = chunk_corpus(store.documents())
    embedder = get_embedder(args.engine)
    mode = SearchMode(args.mode)

    # Annotated to the protocol, not to the first implementation assigned — mypy
    # otherwise infers `HeuristicReranker` and rejects the LLM one on the next line.
    reranker: Reranker = HeuristicReranker()
    if mode is SearchMode.HYBRID_LLM:
        client = get_client("gemini")
        available, reason = client.available()
        if not available:
            print(f"llm reranking unavailable: {reason}")
            return 1
        reranker = LLMReranker(client)

    service = SearchService(BM25Index(chunks), _vector_store(args), embedder, reranker)
    hits = service.search(
        args.search, SearchConfig(mode=mode, engine=args.engine, limit=args.limit)
    )

    print(f"{args.search!r} — {mode.value} over {args.engine}, {len(hits)} hits\n")
    for rank, hit in enumerate(hits, start=1):
        anchor = hit.anchor or "(no anchor)"
        print(f"{rank:>2}. {hit.score:>8.4f}  {hit.source_path}  {anchor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
