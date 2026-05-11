# algorithms_implemented/brute_force/__init__.py
"""Brute-force algorithm package — metadata-only entry point.

Registers lightweight metadata with the shared registry. Imported by both
the API and the worker process; must NOT pull in heavy dependencies
(numpy / pandas). The heavy implementation lives in ``algorithm.py`` and
``_impl/``, loaded only by the worker.
"""

from ...registry import registry
from .metadata import BRUTE_FORCE_METADATA

registry.register_metadata(BRUTE_FORCE_METADATA)
