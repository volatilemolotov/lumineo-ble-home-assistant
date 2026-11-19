"""'Unpair' button: factory-resets a light over the mesh so it can be paired again."""
from __future__ import annotations

import logging

from bleak.exc import BleakError
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LumineoConfigEntry
from .const import CONF_MESH
from .device import MeshError
from .entity import LumineoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: LumineoConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(
        LumineoUnpair(entry, node) for node in entry.runtime_data.nodes if node.get("k")
    )


class LumineoUnpair(LumineoEntity, ButtonEntity):
    _attr_translation_key = "unpair"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = True

    def __init__(self, entry: LumineoConfigEntry, node: dict) -> None:
        super().__init__(entry.runtime_data, node)
        self._entry = entry
        self._attr_unique_id = f"{self._data.mesh_id}-{self._address}-unpair"

    async def async_press(self) -> None:
        try:
            await self._data.mesh.async_reset_node(self._address, bytes.fromhex(self._node["k"]))
        except (BleakError, MeshError) as err:
            raise HomeAssistantError(f"Could not unpair {self._node['n']}: {err}") from err
        _LOGGER.info("%s was reset and removed from the mesh", self._node["n"])
        mesh = dict(self._entry.data[CONF_MESH])
        mesh["nodes"] = [n for n in mesh["nodes"] if n["a"] != self._address]
        hass = self.hass
        hass.config_entries.async_update_entry(self._entry, data={**self._entry.data, CONF_MESH: mesh})
        hass.async_create_task(hass.config_entries.async_reload(self._entry.entry_id))
