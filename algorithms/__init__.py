"""Algorithm package.

Auto-discovers algorithm packages under ``algorithms_implemented``.

Two discovery modes:

- ``autodiscover()`` — lightweight. Imports only each package's ``__init__``
  module, which must register the algorithm's metadata (no heavy deps).
  Safe to call from the API process.

- ``autodiscover(load_implementations=True)`` — also imports each package's
  ``algorithm`` module, which registers the implementation class and may
  pull in heavy dependencies (numpy, cvxpy, pandas, ...). Intended for the
  worker process.
"""

import importlib
import logging
import pkgutil
from pathlib import Path

logger = logging.getLogger(__name__)


def autodiscover(load_implementations: bool = False) -> None:
    """Discover all algorithm packages and register their metadata.

    When ``load_implementations`` is True, also import each package's
    ``algorithm`` module, triggering registration of the implementation class.
    """
    base = Path(__file__).parent / "algorithms_implemented"
    package_prefix = f"{__name__}.algorithms_implemented"

    for module_info in pkgutil.iter_modules([str(base)]):
        if not module_info.ispkg:
            continue

        pkg_name = f"{package_prefix}.{module_info.name}"
        try:
            importlib.import_module(pkg_name)
        except Exception as e:
            logger.exception(
                "Failed to load metadata for '%s': %s", module_info.name, e
            )
            continue

        if load_implementations:
            impl_name = f"{pkg_name}.algorithm"
            try:
                importlib.import_module(impl_name)
            except Exception as e:
                logger.exception(
                    "Failed to load implementation for '%s': %s", module_info.name, e
                )
