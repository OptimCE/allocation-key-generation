# algorithms_implemented/olagsa/__init__.py
"""OLAGSA algorithm package — metadata-only entry point.

Registers lightweight metadata with the shared registry. Imported by both
the API and the worker process; must NOT pull in heavy dependencies
(numpy / cvxpy / pandas). The heavy implementation lives in ``algorithm.py``
and ``_impl/``, loaded only by the worker.
"""

from ...registry import registry
from .metadata import OLAGSA_METADATA

registry.register_metadata(OLAGSA_METADATA)
