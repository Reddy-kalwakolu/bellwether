"""`python -m bellwether.context` — ingest, chunk, embed, search, and evaluate.

Day 7 promised `--chunk`, `--embed` and `--engines all` and shipped none of them,
so its published numbers came from an ad-hoc script and are not reproducible by
anyone reading the repo. That is the gap this closes — and Day 8 adds `--search`
and `--eval` so the retrieval comparison is a command, not a spreadsheet.
"""

from __future__ import annotations

import argparse
import statistics
import time
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
from bellwether.context.retrieval.rerank.base import Reranker, RerankerSpec, RerankResult
from bellwether.context.retrieval.search import SearchConfig, SearchMode, SearchService
from bellwether.context.store import JsonlDocumentStore
from bellwether.context.vectors import (
    COLLECTION,
    InMemoryVectorStore,
    QdrantVectorStore,
    SearchHit,
)
from bellwether.eval.gold import Category, load_gold_set
from bellwether.eval.pooling import build_pool, pool_coverage
from bellwether.eval.report import evaluate, evaluate_category, format_markdown, format_results
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
    parser.add_argument("--eval", action="store_true", help="Score every configuration.")
    parser.add_argument(
        "--gold",
        type=Path,
        default=Path("data/gold/day08-retrieval.json"),
        help="The answer key to score against.",
    )
    parser.add_argument("--pool", action="store_true", help="Print the judging pool.")
    parser.add_argument("--markdown", action="store_true", help="Also print the table as Markdown.")
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

    if args.eval or args.pool:
        return _evaluate(args, store)

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


class _CountingReranker:
    """Wraps a reranker to record what it spent and how often it gave up.

    `LLMReranker` degrades to the fused order on any backend failure and reports
    `usage=None` when it does. That silence is correct — an outage should cost a
    slightly worse ranking, not an error — and it is exactly how a reranker that had
    never successfully run once produced a full page of plausible numbers. Counting
    the degrades turns the eval's most expensive lesson into a line of output.
    """

    def __init__(self, inner: Reranker) -> None:
        self._inner = inner
        self.calls = 0
        self.degraded = 0
        self.tokens = 0
        self.cost_usd = 0.0

    @property
    def spec(self) -> RerankerSpec:
        """The wrapped reranker's own spec."""
        return self._inner.spec

    def available(self) -> tuple[bool, str]:
        """Available exactly when the wrapped reranker is."""
        return self._inner.available()

    def rerank(self, query: str, hits: Sequence[SearchHit], limit: int) -> RerankResult:
        """Delegate, then record the bill or the degrade."""
        result = self._inner.rerank(query, hits, limit)
        self.calls += 1
        if result.usage is None:
            self.degraded += 1
        else:
            self.tokens += result.usage.tokens
            self.cost_usd += result.usage.cost_usd
        return result


def _run_all_modes(
    args: argparse.Namespace,
    query_text: str,
    heuristic: SearchService,
    llm: SearchService | None,
) -> tuple[dict[str, list[SearchHit]], dict[str, float]]:
    """Run every configuration for one query, timing each.

    Two services because `SearchService` holds one reranker: the heuristic one serves
    every mode that does not rerank plus `hybrid-heuristic`, and a second, LLM-backed
    service serves `hybrid-llm`. Both embed the query the same way, so the only thing
    that differs between rows is the stage whose name the row carries.
    """
    hits: dict[str, list[SearchHit]] = {}
    latency: dict[str, float] = {}
    for mode in SearchMode:
        service = llm if mode is SearchMode.HYBRID_LLM else heuristic
        if service is None:
            continue
        config = SearchConfig(mode=mode, engine=args.engine, limit=10)
        started = time.perf_counter()
        hits[mode.value] = service.search(query_text, config)
        latency[mode.value] = (time.perf_counter() - started) * 1000
    return hits, latency


def _evaluate(args: argparse.Namespace, store: JsonlDocumentStore) -> int:
    """Run every configuration over the gold set, then pool or score."""
    goldset = load_gold_set(args.gold)
    chunks = chunk_corpus(store.documents())
    index = BM25Index(chunks)
    embedder = get_embedder(args.engine)
    vectors = _vector_store(args)

    heuristic = SearchService(index, vectors, embedder, HeuristicReranker())
    llm: SearchService | None = None
    counter: _CountingReranker | None = None
    client = get_client("gemini")
    available, reason = client.available()
    if available:
        counter = _CountingReranker(LLMReranker(client))
        llm = SearchService(index, vectors, embedder, counter)
    else:
        print(f"hybrid-llm skipped: {reason}")

    rankings: dict[str, dict[str, list[SearchHit]]] = {}
    # Every sample, not the last one. Overwriting per query and calling the survivor
    # a p50 published `hybrid-heuristic` as faster than the `dense` it is built on —
    # a number that cannot be true, and the tell that it was one noisy sample.
    samples: dict[str, list[float]] = {}
    for query in goldset.queries:
        hits, latency = _run_all_modes(args, query.text, heuristic, llm)
        rankings[query.query_id] = hits
        for mode, elapsed in latency.items():
            samples.setdefault(mode, []).append(elapsed)

    latencies = {mode: statistics.median(values) for mode, values in samples.items()}
    costs = {SearchMode.HYBRID_LLM.value: counter.cost_usd} if counter is not None else {}

    if args.pool:
        for entry in build_pool(goldset.queries, rankings):
            anchor = entry.anchor or "(no anchor)"
            print(f"{entry.query_id}\t{entry.chunk_id}\t{anchor}\t{entry.source_path}")
        return 0

    results = evaluate(goldset, rankings, latencies, costs)
    print(format_results(results, "All queries"))
    for category in Category:
        print()
        print(
            format_results(
                evaluate_category(goldset, rankings, category), f"Category: {category.value}"
            )
        )
    print(f"\npool coverage: {pool_coverage(goldset, rankings):.1%}")

    if counter is not None:
        # A reranker that degrades is silent by design — an outage costs a slightly
        # worse ranking, not an error. That silence once hid a reranker that had
        # never run at all, so the count is reported whether or not it is zero.
        print(
            f"hybrid-llm: {counter.degraded}/{counter.calls} queries degraded to the "
            f"fused order · {counter.tokens} tokens · ${counter.cost_usd:.4f}"
        )

    if args.markdown:
        print("\n" + format_markdown(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
