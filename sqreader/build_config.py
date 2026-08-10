"""Build-time constants compiled into a release binary.

A source checkout ships ``LOCKED = False`` so development and staging keep
working: ``enroll --central-url`` points wherever you like. The packaging step
rewrites this module from ``packaging/entitlements.env`` immediately before
compiling, so a *released binary* carries its central URL as a compiled-in
constant.

That distinction is the whole point. Credentials in ``.env.agent`` are editable
by whoever runs the box; a compiled constant is not. Once ``LOCKED`` is baked
true, neither a hand-edited ``.env.agent`` nor a hostile/compromised enroll
response can redirect this agent's traffic somewhere else — see
``ingest_client._push_base``, the single choke point every outbound request
passes through.
"""
from __future__ import annotations

from urllib.parse import urlsplit

#: Central this build is bound to, e.g. ``"https://squadreader.com"``.
#: Empty in source checkouts; written by the packaging step.
BAKED_CENTRAL_URL: str = ""

#: When true the agent refuses to talk to anything but :data:`BAKED_CENTRAL_URL`.
LOCKED: bool = False


def locked_base() -> str | None:
    """The enforced central base URL, or ``None`` when this build is unlocked.

    Returns ``None`` unless BOTH flags are set, so a half-configured build fails
    open (works normally) rather than closed (refuses every URL, bricking a box
    that was fine before the upgrade).
    """
    if LOCKED and BAKED_CENTRAL_URL:
        return BAKED_CENTRAL_URL.rstrip("/")
    return None


def origin(url: str) -> tuple[str, str]:
    """``(scheme, host:port)`` of *url*, lowercased — the identity we pin on.

    Scheme is part of it deliberately: pinning the host alone would still allow
    an ``https`` → ``http`` downgrade to the same name.
    """
    parts = urlsplit(url if "//" in url else f"//{url}")
    return (parts.scheme.lower(), (parts.netloc or "").lower())


def same_origin(a: str, b: str) -> bool:
    """True when *a* and *b* share scheme + host + port (path may differ)."""
    return origin(a) == origin(b)
