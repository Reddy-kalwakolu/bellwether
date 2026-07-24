"""The table Day 8 publishes, per configuration and per question shape.

The per-category split is the point, not a detail. A headline that says hybrid beats
vector by some percentage, while hiding that the entire margin came from identifier
queries and that it lost on conceptual ones, is exactly the kind of result this
project exists not to produce.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bellwether.eval.gold import Category, GoldQuery, GoldSet
from bellwether.eval.metrics import DEFAULT_K, mrr, ndcg_at_k, recall_at_k
from bellwether.eval.pooling import Rankings


@dataclass(frozen=True)
class ConfigurationResult:
    """One row of the comparison."""

    mode: str
    queries: int
    ndcg: float
    recall: float
    reciprocal_rank: float
    latency_ms: float | None = None
    cost_usd: float | None = None


def _modes(rankings: Rankings) -> list[str]:
    """Every configuration name that appears anywhere, in stable order."""
    names: set[str] = set()
    for by_mode in rankings.values():
        names.update(by_mode)
    return sorted(names)


def _score(
    queries: Sequence[GoldQuery],
    rankings: Rankings,
    mode: str,
    k: int,
) -> tuple[float, float, float]:
    """Mean nDCG, recall and reciprocal rank for one configuration."""
    if not queries:
        return 0.0, 0.0, 0.0

    ndcg_total = 0.0
    recall_total = 0.0
    rank_total = 0.0
    for query in queries:
        # A query this configuration produced no ranking for scores zero rather than
        # being dropped — silently excluding it would flatter the configuration.
        hits = rankings.get(query.query_id, {}).get(mode, [])
        ranked = [hit.chunk_id for hit in hits]
        ndcg_total += ndcg_at_k(ranked, query.judgements, k)
        recall_total += recall_at_k(ranked, query.judgements, k)
        rank_total += mrr(ranked, query.judgements, k)

    count = len(queries)
    return ndcg_total / count, recall_total / count, rank_total / count


def evaluate(
    goldset: GoldSet,
    rankings: Rankings,
    latencies: Mapping[str, float] | None = None,
    costs: Mapping[str, float] | None = None,
    k: int = DEFAULT_K,
) -> list[ConfigurationResult]:
    """One result per configuration, over every query in the gold set."""
    return _evaluate(goldset.queries, rankings, latencies, costs, k)


def evaluate_category(
    goldset: GoldSet,
    rankings: Rankings,
    category: Category,
    k: int = DEFAULT_K,
) -> list[ConfigurationResult]:
    """One result per configuration, over one question shape."""
    return _evaluate(goldset.by_category(category), rankings, None, None, k)


def _evaluate(
    queries: Sequence[GoldQuery],
    rankings: Rankings,
    latencies: Mapping[str, float] | None,
    costs: Mapping[str, float] | None,
    k: int,
) -> list[ConfigurationResult]:
    """Score every configuration over `queries`."""
    results: list[ConfigurationResult] = []
    for mode in _modes(rankings):
        ndcg, recall, rank = _score(queries, rankings, mode, k)
        results.append(
            ConfigurationResult(
                mode=mode,
                queries=len(queries),
                ndcg=ndcg,
                recall=recall,
                reciprocal_rank=rank,
                latency_ms=(latencies or {}).get(mode),
                cost_usd=(costs or {}).get(mode),
            )
        )
    return results


def format_results(results: Sequence[ConfigurationResult], title: str) -> str:
    """The comparison as a fixed-width table for the terminal and the devlog."""
    header = (
        f"{'configuration':<20}{'queries':>9}{'nDCG@10':>10}"
        f"{'recall@10':>11}{'MRR':>8}{'p50 ms':>9}{'cost USD':>11}"
    )
    lines = [title, "=" * len(header), header, "-" * len(header)]
    for result in results:
        latency = f"{result.latency_ms:.1f}" if result.latency_ms is not None else "-"
        cost = f"{result.cost_usd:.4f}" if result.cost_usd is not None else "-"
        lines.append(
            f"{result.mode:<20}{result.queries:>9}{result.ndcg:>10.3f}"
            f"{result.recall:>11.3f}{result.reciprocal_rank:>8.3f}{latency:>9}{cost:>11}"
        )
    return "\n".join(lines)


def format_markdown(results: Sequence[ConfigurationResult]) -> str:
    """The same table as Markdown, for the devlog and the running doc."""
    lines = [
        "| Configuration | nDCG@10 | recall@10 | MRR | p50 ms | cost USD |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        latency = f"{result.latency_ms:.1f}" if result.latency_ms is not None else "—"
        cost = f"{result.cost_usd:.4f}" if result.cost_usd is not None else "—"
        lines.append(
            f"| {result.mode} | {result.ndcg:.3f} | {result.recall:.3f} "
            f"| {result.reciprocal_rank:.3f} | {latency} | {cost} |"
        )
    return "\n".join(lines)
