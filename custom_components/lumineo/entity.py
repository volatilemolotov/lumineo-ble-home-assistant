"""Shared per-light state and base entity."""
from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from . import LumineoData
from .const import DOMAIN
from .mesh import SPEED_DEFAULT


@dataclass
class LightSettings:
    """Effect settings shared between the light, speed and palette entities of one node."""
    speed: int = SPEED_DEFAULT
    palette: str = "Christmas"
    listeners: list = field(default_factory=list)


_SETTINGS: dict[tuple[str, int], LightSettings] = {}


def settings_for(data: LumineoData, address: int) -> LightSettings:
    return _SETTINGS.setdefault((data.mesh_id, address), LightSettings())


class LumineoEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, data: LumineoData, node: dict) -> None:
        self._data = data
        self._node = node
        self._address: int = node["a"]
        self._settings = settings_for(data, self._address)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{data.mesh_id}-{self._address}")},
            connections={(CONNECTION_BLUETOOTH, node["m"].upper())},
            name=node["n"],
            manufacturer="Lumineo / Kaemingk",
            model="Dancing Lights (Telink SIG Mesh)",
        )
