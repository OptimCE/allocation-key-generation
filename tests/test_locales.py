"""i18n smoke tests.

Every error key declared in shared/custom_errors.py must resolve to a
non-empty, non-key string in every supported locale. A regression here
means a user would see a raw `ERRORS.SUBSCRIPTION.NOT_SUBSCRIBED`-style
key in the response body.
"""

import pytest

from algorithms import autodiscover as _autodiscover
from algorithms.registry import registry as _registry
from core.i18n import SUPPORTED_LOCALES, translate
from shared.custom_errors import errors

# All translation keys declared in shared/custom_errors.py. Re-derived
# from the Error registry so adding a new error code surfaces here as a
# missing translation rather than a silent gap.
_ALL_KEYS = sorted(
    {
        e.key
        for domain in (errors.auth, errors.subscription, errors.generation)
        for e in domain.__class__.__dict__.values()
        if hasattr(e, "key")
    }
)


# Collect parameter i18n keys from all registered algorithms' input schemas.
# conftest._register_algorithms (autouse, session-scoped) populates the registry
# before tests run, but module-level code executes at collection time — guard with
# the same idempotency pattern conftest uses.
if not _registry.list_all():
    _autodiscover()

_ALL_PARAMETER_KEYS = sorted(
    {
        value
        for meta in _registry.list_all()
        for field_schema in meta.input_schema.model_json_schema().get("properties", {}).values()
        for key in ("title", "description")
        if (value := field_schema.get(key)) and value.startswith("PARAMETERS.")
    }
)


def test_supported_locales_includes_fr_en_de_nl():
    assert {"fr", "en", "de", "nl"} <= SUPPORTED_LOCALES


@pytest.mark.parametrize("locale", sorted(SUPPORTED_LOCALES))
@pytest.mark.parametrize("key", _ALL_KEYS)
def test_translate_returns_localized_string_for_every_supported_locale(locale: str, key: str):
    translated = translate(key, locale)
    assert translated != key, (
        f"Key {key!r} is missing in locale {locale!r} " f"(translate returned the bare key)"
    )
    assert isinstance(translated, str)
    assert translated.strip(), f"Empty translation for {key!r} in {locale!r}"


@pytest.mark.parametrize("locale", sorted(SUPPORTED_LOCALES))
@pytest.mark.parametrize("key", _ALL_PARAMETER_KEYS)
def test_parameter_keys_resolve_in_every_supported_locale(locale: str, key: str):
    translated = translate(key, locale)
    assert translated != key, (
        f"Parameter key {key!r} is missing in locale {locale!r} "
        f"(translate returned the bare key)"
    )
    assert isinstance(translated, str)
    assert translated.strip(), f"Empty translation for {key!r} in {locale!r}"
