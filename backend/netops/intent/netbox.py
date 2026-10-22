"""NetBox as an inventory source: maps /api/dcim/devices/ onto the same `Inventory` as inventory.yaml."""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Any

from netops.enums import DeviceRole, Platform
from netops.intent.models import Inventory, InventoryDevice

logger = logging.getLogger(__name__)

Fetch = Callable[[str, str], dict[str, Any]]


def _http_get(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Token {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def load_netbox_inventory(
    base_url: str, token: str, *, default_auth_profile: str = "lab", fetch: Fetch = _http_get
) -> Inventory:
    """Devices without a primary IPv4, or with an unknown platform or role, are skipped with a warning."""
    url: str | None = f"{base_url.rstrip('/')}/api/dcim/devices/?limit=500"
    devices: list[InventoryDevice] = []
    while url:
        page = fetch(url, token)
        for raw in page.get("results") or []:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            try:
                ip = str((raw.get("primary_ip4") or {}).get("address") or "").split("/")[0]
                platform = (raw.get("platform") or {}).get("slug")
                role = (raw.get("role") or raw.get("device_role") or {}).get("slug")
                site_slug = (raw.get("site") or {}).get("slug")
                kwargs: dict[str, Any] = {}
                if site_slug:
                    kwargs["fabric_id"] = site_slug
                    kwargs["site"] = site_slug
                devices.append(
                    InventoryDevice(
                        hostname=name,
                        management_ip=ip,
                        platform=Platform(platform),
                        role=DeviceRole(role),
                        auth_profile=(raw.get("custom_fields") or {}).get("auth_profile") or default_auth_profile,
                        **kwargs,
                    )
                )
            except (ValueError, TypeError, AttributeError) as exc:
                logger.warning("NetBox device %r skipped: %s", name, exc)
        url = page.get("next")
    return Inventory(devices=devices)
