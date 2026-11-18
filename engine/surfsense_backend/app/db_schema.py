"""Startup schema checks that must not drift from the embedding config."""

import logging
from collections.abc import Mapping

logger = logging.getLogger(__name__)


def check_embedding_dimension_schema(
    db_dimensions: Mapping[str, int],
    configured_dimension: int,
) -> None:
    """Refuse to start when a pgvector column disagrees with the config.

    ``db_dimensions`` maps each existing embedding column, keyed by
    ``"table.column"``, to its dimension (``atttypmod`` for pgvector). Columns
    or tables that do not exist yet are simply absent, which is fine on a
    fresh database. Unconstrained (unsized) columns are skipped by the caller
    before this check.
    """
    mismatched = {
        column: db_dimension
        for column, db_dimension in db_dimensions.items()
        if db_dimension != configured_dimension
    }
    if not mismatched:
        return

    details = ", ".join(
        f"{column}=vector({db_dimension})"
        for column, db_dimension in sorted(mismatched.items())
    )
    message = (
        f"Embedding dimension mismatch: {details} but the configured "
        f"embedding dimension is {configured_dimension}. Refusing to start: "
        "knowledge-base search would fail with 'expected N dimensions'. Fix "
        "the database columns or set EMBEDDING_DIMENSION to match them."
    )
    logger.error("%s", message)
    raise RuntimeError(message)
