# algorithms/registry.py

from .base import Algorithm, AlgorithmMetadata


class AlgorithmRegistry:
    """In-memory registry for algorithm metadata and implementations.

    Metadata is registered at import time by each algorithm package's
    ``__init__`` (lightweight, API-safe). Implementation classes are
    registered by ``algorithm.py`` modules, imported only by the worker.
    """

    def __init__(self) -> None:
        self._metadata: dict[str, AlgorithmMetadata] = {}
        self._implementations: dict[str, type[Algorithm]] = {}

    # ---- Registration ------------------------------------------------------
    def register_metadata(self, meta: AlgorithmMetadata) -> None:
        if meta.name in self._metadata:
            raise ValueError(f"Algorithm '{meta.name}' already registered")
        self._metadata[meta.name] = meta

    def register_implementation(self, cls: type[Algorithm]) -> None:
        name = cls.metadata.name
        if name not in self._metadata:
            # Register metadata opportunistically if the impl brings its own.
            self._metadata[name] = cls.metadata
        self._implementations[name] = cls

    # ---- Lookup ------------------------------------------------------------
    def metadata(self, name: str) -> AlgorithmMetadata:
        if name not in self._metadata:
            raise KeyError(f"Unknown algorithm: {name}")
        return self._metadata[name]

    def implementation(self, name: str) -> type[Algorithm]:
        if name not in self._implementations:
            raise KeyError(f"No implementation loaded for: {name}")
        return self._implementations[name]

    def list_all(self) -> list[AlgorithmMetadata]:
        return list(self._metadata.values())

    def __contains__(self, name: str) -> bool:
        return name in self._metadata


registry = AlgorithmRegistry()
