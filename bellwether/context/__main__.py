"""`python -m bellwether.context` — ingest the repo and print what changed."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from bellwether.context.config import settings
from bellwether.context.pipeline import format_report, ingest
from bellwether.context.store import JsonlDocumentStore


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ingestion pipeline against a repo root and write the corpus."""
    parser = argparse.ArgumentParser(description="Ingest the BELLWETHER corpus.")
    parser.add_argument("--root", type=Path, default=settings.repo_root)
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing the corpus.",
    )
    args = parser.parse_args(argv)

    root: Path = args.root
    corpus_path: Path = args.corpus or root / settings.corpus_path

    store = JsonlDocumentStore(corpus_path)
    report = ingest(root, store)
    if not args.dry_run:
        store.flush()

    print(format_report(report))
    print("dry run: nothing written" if args.dry_run else f"corpus at: {corpus_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
