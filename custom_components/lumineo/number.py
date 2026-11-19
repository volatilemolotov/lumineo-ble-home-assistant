"""Effect speed control."""
from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LumineoConfigEntry
from .entity import LumineoEntity
from .mesh import SPEED_DEFAULT, SPEED_MAX, SPEED_MIN


async def async_setup_entry(hass: HomeAssistant, entry: LumineoConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(LumineoSpeed(entry.runtime_data, node) for node in entry.runtime_data.nodes)


class LumineoSpeed(LumineoEntity, RestoreNumber):
    _attr_translation_key = "effect_speed"
    _attr_native_min_value = SPEED_MIN
    _attr_native_max_value = SPEED_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, data, node) -> None:
        super().__init__(data, node)
        self._attr_unique_id = f"{data.mesh_id}-{self._address}-speed"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._settings.speed = int(last.native_value)

    @property
    def native_value(self) -> float:
        return self._settings.speed or SPEED_DEFAULT

    async def async_set_native_value(self, value: float) -> None:
        self._settings.speed = int(value)
        self.async_write_ha_state()
        for cb in list(self._settings.listeners):
            await cb()
