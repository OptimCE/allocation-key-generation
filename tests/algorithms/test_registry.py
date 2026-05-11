"""Algorithm registry guardrail tests.

``algorithms.autodiscover`` runs at API startup and worker startup. It logs
failures per algorithm but does NOT raise — a malformed algorithm package
would silently disappear from ``registry.list_all()`` in production. These
tests close that gap by asserting the post-autodiscover registry state:

* the known algorithms are present (regression detector for autodiscover);
* every registered metadata is well-formed (name, queue, input_schema);
* every input schema is a real Pydantic model with a generatable JSON schema
  (Pydantic raises here on garbage schemas, e.g. unresolved forward refs);
* names are unique across the global registry;
* the registry's ``register_metadata`` rejects duplicate names with
  ``ValueError`` (enforces the contract directly on a fresh registry).

The session-scoped ``_register_algorithms`` fixture in tests/conftest.py
already calls ``autodiscover()`` once per session, so by the time these
tests run the global registry is populated.
"""

import pytest
from pydantic import BaseModel

from algorithms.base import AlgorithmInput, AlgorithmMetadata
from algorithms.registry import AlgorithmRegistry, registry


def _make_dummy_metadata(name: str) -> AlgorithmMetadata:
    """Build a minimal valid AlgorithmMetadata for registry-contract tests."""

    class _DummyInput(AlgorithmInput):
        value: int

    return AlgorithmMetadata(
        name=name,
        description="dummy algorithm for tests",
        version="1.0",
        queue=f"optimce.allocation.{name}",
        input_schema=_DummyInput,
    )


def test_autodiscover_registers_known_algorithms():
    """Sanity check: the two real algorithms surface after autodiscover.

    If this fails, autodiscover broke (import error in a package's
    ``__init__``) and would've silently logged in production.
    """
    assert "brute_force" in registry
    assert "olagsa" in registry


def test_every_registered_algorithm_has_valid_metadata():
    """Every algorithm in the global registry must have a well-formed
    metadata object. Catches a regression where a future algorithm
    registers metadata with empty / wrong-typed fields.
    """
    metas = registry.list_all()
    assert metas, "registry is empty — autodiscover did not run or found nothing"

    for meta in metas:
        assert isinstance(
            meta, AlgorithmMetadata
        ), f"registry entry is not AlgorithmMetadata: {type(meta).__name__}"
        assert (
            isinstance(meta.name, str) and meta.name
        ), f"empty/invalid name on metadata: {meta!r}"
        assert (
            isinstance(meta.queue, str) and meta.queue
        ), f"empty/invalid queue on metadata for {meta.name!r}"
        assert (
            isinstance(meta.description, str) and meta.description
        ), f"empty description on metadata for {meta.name!r}"
        assert isinstance(meta.input_schema, type), (
            f"input_schema is not a class for {meta.name!r}: "
            f"got {type(meta.input_schema).__name__}"
        )
        assert issubclass(
            meta.input_schema, BaseModel
        ), f"input_schema for {meta.name!r} does not subclass pydantic.BaseModel"


def test_every_registered_input_schema_emits_a_valid_json_schema():
    """Pydantic raises on malformed schemas (unresolved refs, recursive
    cycles without ref, etc.) when ``model_json_schema()`` is called.
    Calling it here forces every registered schema through the same
    serialization path the API uses for ``GET /generation/algorithms``.
    """
    for meta in registry.list_all():
        schema = meta.input_schema.model_json_schema()
        assert isinstance(
            schema, dict
        ), f"model_json_schema for {meta.name!r} did not return a dict"
        assert (
            "properties" in schema or schema.get("type") == "object"
        ), f"input schema for {meta.name!r} has no properties: {schema!r}"
        assert callable(
            getattr(meta.input_schema, "model_validate", None)
        ), f"input_schema for {meta.name!r} lacks model_validate"


def test_registered_algorithm_names_are_unique():
    """Defensive: register_metadata raises on duplicates today, but if the
    semantics ever change to overwrite-silently, this catches it.
    """
    metas = registry.list_all()
    names = [m.name for m in metas]
    assert len(names) == len(
        set(names)
    ), f"duplicate algorithm names in registry: {names}"


def test_register_metadata_rejects_duplicate_name():
    """Direct contract test: registering the same name twice raises ValueError.

    Uses a fresh ``AlgorithmRegistry()`` so we never mutate the global
    ``algorithms.registry.registry`` (which would leak into other tests).
    """
    fresh = AlgorithmRegistry()
    meta = _make_dummy_metadata("duplicate_name_check")

    fresh.register_metadata(meta)

    with pytest.raises(ValueError, match="already registered"):
        fresh.register_metadata(meta)
