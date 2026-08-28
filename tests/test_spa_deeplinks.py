"""A deep link into the viewer has to survive a hard reload.

The viewer resolves its own URLs in the browser, so `/viewer/<recording-id>`
never existed on the server — it worked only as long as you arrived there by
clicking. Reload the page, or open the link a teammate sent you, and the
request reached the server for real and got a 404.

The other half of the claim matters as much: a MISSING FILE must still be a
404. Serving index.html in place of a bundle turns "not found" into a syntax
error three layers away.
"""
from __future__ import annotations

import urllib.error
import urllib.request

from sqreader.httpsrv import _looks_like_spa_route, _TickBeat, serve_in_background


def _frontend(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>viewer</title>",
                                  encoding="utf-8")
    (d / "assets" / "app-abc123.js").write_text("export {}", encoding="utf-8")
    return d


def _server(tmp_path, *, frontend=True):
    srv = serve_in_background(
        "127.0.0.1", 0, _TickBeat(),
        frontend_dir=_frontend(tmp_path) if frontend else None)
    return srv, srv.server_address[1]


def _get(port, path, accept="text/html,application/xhtml+xml"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if accept:
        req.add_header("Accept", accept)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def _status(port, path, accept="text/html"):
    try:
        return _get(port, path, accept)[0]
    except urllib.error.HTTPError as e:
        return e.code


# -- the predicate on its own ------------------------------------------------

def test_a_path_with_no_file_extension_is_an_app_route():
    assert _looks_like_spa_route("/viewer/2026-08-27_012533_Fallujah", None)
    assert _looks_like_spa_route("/matches", None)
    assert _looks_like_spa_route("/", None)


def test_something_that_looks_like_a_file_is_not():
    assert not _looks_like_spa_route("/missing.png", "image/png,*/*")
    assert not _looks_like_spa_route("/robots.txt", "text/plain")


def test_but_a_navigation_to_a_dotted_route_still_is():
    """A hard reload asks for HTML, whatever the URL looks like."""
    assert _looks_like_spa_route(
        "/viewer/2026-08-27_012533_v1.2", "text/html,application/xhtml+xml")


# -- over a real socket ------------------------------------------------------

def test_a_deep_link_serves_the_app(tmp_path):
    srv, port = _server(tmp_path)
    try:
        status, ctype, body = _get(port, "/viewer/2026-08-27_012533_Fallujah")
        assert status == 200
        assert ctype.startswith("text/html")
        assert b"<title>viewer</title>" in body
    finally:
        srv.shutdown()


def test_the_named_routes_still_work(tmp_path):
    srv, port = _server(tmp_path)
    try:
        for path in ("/", "/viewer", "/viewer.html", "/viewer-next"):
            assert _status(port, path) == 200
    finally:
        srv.shutdown()


def test_a_real_asset_is_still_served_as_itself(tmp_path):
    srv, port = _server(tmp_path)
    try:
        status, ctype, body = _get(port, "/assets/app-abc123.js",
                                   accept="*/*")
        assert status == 200
        assert "javascript" in ctype
        assert body == b"export {}"
    finally:
        srv.shutdown()


def test_a_missing_asset_is_a_404_not_the_app(tmp_path):
    """The important half. An HTML body under a .js URL is a parse error in a
    stack trace that says nothing about the real problem."""
    srv, port = _server(tmp_path)
    try:
        assert _status(port, "/assets/gone-000000.js", accept="*/*") == 404
        assert _status(port, "/missing.png", accept="image/png") == 404
    finally:
        srv.shutdown()


def test_an_unknown_api_path_is_still_a_404(tmp_path):
    """The API is not the app, and a typo in an endpoint must say so rather
    than returning a page that parses as nothing."""
    srv, port = _server(tmp_path)
    try:
        assert _status(port, "/api/nonesuch") == 404
    finally:
        srv.shutdown()


def test_without_a_frontend_nothing_is_invented(tmp_path):
    srv, port = _server(tmp_path, frontend=False)
    try:
        assert _status(port, "/viewer/anything") == 404
    finally:
        srv.shutdown()
