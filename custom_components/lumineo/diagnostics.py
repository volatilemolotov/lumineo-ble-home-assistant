"""Diagnostics download: mesh settings (keys redacted), GATT layout and recent mesh traffic."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import LumineoConfigEntry

# meshPwd is the network key, "k" is a light's device key. With them anyone nearby
# could control (or factory-reset) the lights, so they never leave Home Assistant.
TO_REDACT = {"meshPwd", "k", "share"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: LumineoConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data
    link = data.mesh.link
    crypto = data.crypto
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "network_id": crypto.network_id.hex(),
        "src": f"{crypto.src:#06x}",
        "nid": f"{crypto.nid:#04x}",
        "aid": f"{crypto.aid:#04x}",
        "connected": link.connected,
        "gatt_services": link.services,
        "history": [list(h) for h in link.history],
    }
