"""Per-route body-size cap tests for the request-limits middleware.

The generation upload is ``POST /`` (generation_routes is mounted without a
prefix; the ``/generation`` prefix in the public API is added by KrakenD), so it
must receive the larger ``UPLOAD_MAX_BODY_BYTES`` cap; every other route keeps
the conservative ``MAX_BODY_BYTES`` default.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.middleware import request_limits


def _request(method: str, path: str) -> SimpleNamespace:
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def test_upload_route_gets_large_cap():
    req = _request("POST", "/")
    assert request_limits._max_body_for(req) == request_limits.UPLOAD_MAX_BODY_BYTES


def test_get_root_keeps_default_cap():
    # The upload cap is POST-only; a GET on the same path keeps the default.
    req = _request("GET", "/")
    assert request_limits._max_body_for(req) == request_limits.MAX_BODY_BYTES


def test_save_route_keeps_default_cap():
    # POST /save is a JSON route on the same router — no upload cap.
    req = _request("POST", "/save")
    assert request_limits._max_body_for(req) == request_limits.MAX_BODY_BYTES


def test_other_post_route_keeps_default_cap():
    req = _request("POST", "/health/readiness")
    assert request_limits._max_body_for(req) == request_limits.MAX_BODY_BYTES


def test_gateway_prefixed_path_is_not_what_gets_matched():
    # Regression guard: the cap must key off the path the APP sees, not the
    # public KrakenD path. `_UPLOAD_ROUTES` once held ("POST", "/generation/"),
    # which no request to this service ever matches — so every declared-length
    # upload over 2 MB was rejected with 413 by the middleware.
    req = _request("POST", "/generation/")
    assert request_limits._max_body_for(req) == request_limits.MAX_BODY_BYTES
