from __future__ import annotations

import ipaddress
import json
import os
import urllib.request
from urllib.parse import urlsplit


def validate_url(url: str, allow_remote: bool):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.fragment:
        raise ValueError("Model URL must be HTTP(S) without credentials or fragments")
    local = p.hostname.lower() == "localhost"
    try:
        local = local or ipaddress.ip_address(p.hostname).is_loopback
    except ValueError:
        pass
    if not local and not allow_remote:
        raise ValueError("Remote model disabled; explicitly enable allow_remote to send report facts off this Mac")
    if not local and p.scheme != "https":
        raise ValueError("Remote endpoints require HTTPS")
    return p


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Endpoint redirects are not allowed")


def summarize(facts: list[dict], opts: dict) -> dict | None:
    """Legacy compatibility boundary. Truncated card summaries are intentionally gone."""
    if opts["mode"] == "off":
        return None
    raise ValueError("Card-only summaries were retired. Generate a report through the evidence analysis pipeline.")
