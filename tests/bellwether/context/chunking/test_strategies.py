"""The four strategies: where each one cuts, and what it names the pieces."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from bellwether.context.chunking import markdown, openapi, python_ast, window
from bellwether.context.chunking.router import chunk_corpus, chunk_document, strategy_for
from bellwether.context.documents import Document, SourceType, build_document
from bellwether.context.pipeline import ingest
from bellwether.context.store import InMemoryDocumentStore

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[4]

MODULE_SOURCE = '''"""A module docstring."""

from __future__ import annotations

CONSTANT = 3


def first(value: int) -> int:
    """Double it."""
    return value * 2


@decorated
def second() -> None:
    """Decorated on purpose."""
    return None


class Population:
    """A class body stays whole."""

    def member(self) -> str:
        return "member-0001"
'''


def _document(
    source_path: str, source_type: SourceType, content: str, **attributes: str
) -> Document:
    return build_document(
        source_path=source_path,
        source_type=source_type,
        component="test",
        title=source_path,
        content=content,
        ingested_at=NOW,
        attributes=dict(attributes),
    )


# --- routing -----------------------------------------------------------------


def test_each_kind_of_document_gets_the_strategy_it_deserves() -> None:
    assert strategy_for("code")[0] == "python_ast"
    assert strategy_for("openapi")[0] == "openapi"
    assert strategy_for("adr")[0] == "markdown"
    assert strategy_for("devlog")[0] == "markdown"
    assert strategy_for("config")[0] == "window"


# --- python -------------------------------------------------------------------


def test_python_splits_at_symbol_boundaries() -> None:
    document = _document("substrate/x/main.py", "code", MODULE_SOURCE, module="substrate.x.main")
    found = python_ast.pieces(document)
    assert found is not None
    anchors = [piece.anchor for piece in found]
    assert "substrate.x.main.first" in anchors
    assert "substrate.x.main.second" in anchors
    assert "substrate.x.main.Population" in anchors
    assert "substrate.x.main · module scope" in anchors


def test_a_decorator_stays_with_the_function_it_decorates() -> None:
    document = _document("substrate/x/main.py", "code", MODULE_SOURCE, module="substrate.x.main")
    found = python_ast.pieces(document)
    assert found is not None
    second = next(piece for piece in found if piece.anchor == "substrate.x.main.second")
    # The route decorator is often the most retrievable line in a FastAPI module.
    assert "@decorated" in second.text


def test_a_class_body_is_not_torn_apart() -> None:
    document = _document("substrate/x/main.py", "code", MODULE_SOURCE, module="substrate.x.main")
    found = python_ast.pieces(document)
    assert found is not None
    population = next(piece for piece in found if piece.anchor.endswith("Population"))  # type: ignore[union-attr]
    assert "def member" in population.text


def test_module_scope_keeps_the_imports_and_constants() -> None:
    document = _document("substrate/x/main.py", "code", MODULE_SOURCE, module="substrate.x.main")
    found = python_ast.pieces(document)
    assert found is not None
    scope = next(piece for piece in found if piece.anchor and "module scope" in piece.anchor)
    assert "CONSTANT = 3" in scope.text
    assert "from __future__" in scope.text


def test_unparseable_python_declines_rather_than_raising() -> None:
    assert python_ast.pieces(_document("substrate/x/broken.py", "code", "def (:\n")) is None


def test_a_file_that_declines_still_produces_chunks() -> None:
    chunks = chunk_document(_document("substrate/x/broken.py", "code", "def (:\nvalue = 1\n"))
    assert chunks
    # Losing a document because it does not parse would be far worse than windowing it.
    assert chunks[0].provenance.strategy == "window"


def test_pieces_are_ordered_by_where_they_appear() -> None:
    document = _document("substrate/x/main.py", "code", MODULE_SOURCE, module="substrate.x.main")
    found = python_ast.pieces(document)
    assert found is not None
    starts = [piece.line_start for piece in found]
    assert starts == sorted(starts)


# --- markdown -----------------------------------------------------------------

MARKDOWN_SOURCE = """Front matter before any heading.

# ADR-0005

Context sentence.

## Alternatives considered

- rejected, because it puts test code on the serving path

### A proxy

Deeper still.

## Consequences

It breaks the platform for real.
"""


def test_markdown_carries_the_full_heading_path() -> None:
    found = markdown.pieces(_document("docs/adr/0005-x.md", "adr", MARKDOWN_SOURCE))
    assert found is not None
    anchors = [piece.anchor for piece in found]
    assert "ADR-0005" in anchors
    assert "ADR-0005 › Alternatives considered" in anchors
    assert "ADR-0005 › Alternatives considered › A proxy" in anchors
    assert "ADR-0005 › Consequences" in anchors


def test_a_section_keeps_its_own_heading_line() -> None:
    found = markdown.pieces(_document("docs/adr/0005-x.md", "adr", MARKDOWN_SOURCE))
    assert found is not None
    section = next(piece for piece in found if piece.anchor == "ADR-0005 › Consequences")
    # A fragment that has lost its heading cannot be cited or understood.
    assert section.text.startswith("## Consequences")


def test_front_matter_has_no_heading_to_be_named_by() -> None:
    found = markdown.pieces(_document("docs/adr/0005-x.md", "adr", MARKDOWN_SOURCE))
    assert found is not None
    assert found[0].anchor is None
    assert "Front matter" in found[0].text


def test_a_heading_inside_a_code_fence_is_not_a_heading() -> None:
    source = "# Real\n\n```\n# not a heading\n```\n\n## Also real\n"
    found = markdown.pieces(_document("docs/x.md", "readme", source))
    assert found is not None
    assert [piece.anchor for piece in found] == ["Real", "Real › Also real"]


def test_markdown_without_headings_declines() -> None:
    assert markdown.pieces(_document("docs/x.md", "readme", "just a paragraph\n")) is None


# --- openapi ------------------------------------------------------------------

SPEC = json.dumps(
    {
        "openapi": "3.1.0",
        "info": {"title": "campaign-service"},
        "paths": {
            "/campaigns": {
                "get": {"summary": "List campaigns"},
                "post": {"summary": "Open a campaign"},
            },
            "/health": {"get": {"summary": "Health"}},
        },
        "components": {"schemas": {"CampaignRead": {"type": "object"}}},
    },
    indent=2,
    sort_keys=True,
)


def test_an_operation_is_the_unit_people_actually_ask_about() -> None:
    found = openapi.pieces(_document("openapi/campaign-service.json", "openapi", SPEC))
    assert found is not None
    anchors = [piece.anchor for piece in found]
    assert "GET /campaigns" in anchors
    assert "POST /campaigns" in anchors
    assert "GET /health" in anchors


def test_component_schemas_get_their_own_pieces() -> None:
    found = openapi.pieces(_document("openapi/campaign-service.json", "openapi", SPEC))
    assert found is not None
    assert "schema CampaignRead" in [piece.anchor for piece in found]


def test_an_operation_names_the_service_it_belongs_to() -> None:
    found = openapi.pieces(_document("openapi/campaign-service.json", "openapi", SPEC))
    assert found is not None
    assert "campaign-service" in found[0].text


def test_a_spec_that_is_not_json_declines() -> None:
    assert openapi.pieces(_document("openapi/x.json", "openapi", "not json{")) is None


# --- windowing and capping ----------------------------------------------------


def test_an_oversized_piece_keeps_its_anchor_across_the_split() -> None:
    long_text = "".join(f"line {index} of a very long function body\n" for index in range(200))
    capped = window.cap([window.Piece(long_text, "module.enormous", 1, 200)])
    assert len(capped) > 1
    assert all(piece.anchor is not None for piece in capped)
    assert all("module.enormous" in str(piece.anchor) for piece in capped)
    assert capped[0].anchor == "module.enormous · part 1"


def test_no_capped_piece_exceeds_the_limit() -> None:
    long_text = "".join(f"line {index}\n" for index in range(2000))
    capped = window.cap([window.Piece(long_text, None, 1, 2000)])
    assert all(len(piece.text) <= window.MAX_CHARS for piece in capped)


def test_capping_drops_pieces_that_are_only_whitespace() -> None:
    assert window.cap([window.Piece("   \n\n", "empty", 1, 2)]) == []


def test_windowing_claims_no_structure() -> None:
    found = window.pieces(_document("docker-compose.yml", "config", "services:\n  db: {}\n"))
    assert [piece.anchor for piece in found] == [None]


# --- the whole corpus ---------------------------------------------------------


def test_chunk_indices_are_sequential_within_a_document() -> None:
    document = _document("substrate/x/main.py", "code", MODULE_SOURCE, module="substrate.x.main")
    chunks = chunk_document(document)
    assert [chunk.provenance.chunk_index for chunk in chunks] == list(range(len(chunks)))


def test_every_chunk_of_the_real_corpus_is_citable_and_bounded() -> None:
    store = InMemoryDocumentStore()
    ingest(REPO_ROOT, store)
    chunks = chunk_corpus(store.documents())

    assert len(chunks) > 200
    assert all(chunk.text.strip() for chunk in chunks)
    assert all(len(chunk.text) <= window.MAX_CHARS for chunk in chunks)
    assert all(chunk.provenance.line_start >= 1 for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)


def test_the_real_corpus_is_mostly_cut_structurally_not_windowed() -> None:
    store = InMemoryDocumentStore()
    ingest(REPO_ROOT, store)
    chunks = chunk_corpus(store.documents())

    strategies = {chunk.provenance.strategy for chunk in chunks}
    assert {"python_ast", "markdown", "openapi"} <= strategies
    windowed = sum(1 for chunk in chunks if chunk.provenance.strategy == "window")
    assert windowed / len(chunks) < 0.15
