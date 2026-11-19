"""Colour palette for multi-colour effects."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import LumineoConfigEntry
from .entity import LumineoEntity
from .mesh import THEMES


async def async_setup_entry(hass: HomeAssistant, entry: LumineoConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(LumineoPalette(entry.runtime_data, node) for node in entry.runtime_data.nodes)


class LumineoPalette(LumineoEntity, SelectEntity, RestoreEntity):
    _attr_translation_key = "effect_palette"
    _attr_options = list(THEMES)
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, data, node) -> None:
        super().__init__(data, node)
        self._attr_unique_id = f"{data.mesh_id}-{self._address}-palette"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state in THEMES:
            self._settings.palette = last.state

    @property
    def current_option(self) -> str:
        return self._settings.palette

    async def async_select_option(self, option: str) -> None:
        self._settings.palette = option
        self.async_write_ha_state()
        for cb in list(self._settings.listeners):
            await cb()
