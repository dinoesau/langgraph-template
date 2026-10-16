"""S5a grounded retrieval: keyword-ranked policy chunks, offline by design.

The store stays deterministic and dependency-free so unit tests run with
no network and no embeddings. A real embedder can replace the scoring
later behind the same ``RetrieverStore`` boundary.
"""

from typing import Protocol

DEFAULT_TOP_K = 3

DEFAULT_POLICY_DOCS: tuple[str, ...] = (
    "Refund policy: full refunds within 30 days of purchase with a receipt.",
    "Refund policy: digital goods are refundable within 14 days of download.",
    "Shipping policy: standard shipping takes 5 to 7 business days.",
)


class RetrieverStore(Protocol):
    """Sync top-k chunk boundary; keyword or vector stores both satisfy it."""

    def search(self, query: str, limit: int) -> list[str]: ...


def _tokens(text: str) -> set[str]:
    return {part for part in text.lower().split() if part.strip(".,;:!?()")}


class InMemoryRetrieverStore:
    """Deterministic keyword-overlap rank over seeded chunks, no I/O."""

    def __init__(self, docs: list[str] | tuple[str, ...] = DEFAULT_POLICY_DOCS) -> None:
        self._docs = list(docs)

    def seed(self, docs: list[str]) -> None:
        """Add fixed chunks for tests and local work without embeddings."""
        self._docs.extend(docs)

    def search(self, query: str, limit: int) -> list[str]:
        if limit <= 0 or not query.strip():
            return []
        scored = sorted(
            self._docs,
            key=lambda doc: (len(_tokens(query) & _tokens(doc)), doc),
            reverse=True,
        )
        return [doc for doc in scored[:limit] if _tokens(query) & _tokens(doc)]
