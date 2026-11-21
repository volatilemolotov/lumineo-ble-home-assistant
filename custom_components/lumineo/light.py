"""Light entity for Lumineo strings."""
from __future__ import annotations

import logging
from typing import Any

from bleak.exc import BleakError
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import LumineoConfigEntry
from .device import MeshError
from .entity import LumineoEntity
from .mesh import (
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    EFFECTS,
    POWER_STATUS_OP,
    SCENE_STATIC,
    THEMES,
    hex_rgb,
    vendor_brightness,
    vendor_power,
    vendor_power_get,
    vendor_scene,
)

_LOGGER = logging.getLogger(__name__)
EFFECT_STATIC = "Static"


async def async_setup_entry(hass: HomeAssistant, entry: LumineoConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities(LumineoLight(entry.runtime_data, node) for node in entry.runtime_data.nodes)


def _ha_to_index(b: int) -> int:
    return round(BRIGHTNESS_MIN + (BRIGHTNESS_MAX - BRIGHTNESS_MIN) * (max(1, b) - 1) / 254)


class LumineoLight(LumineoEntity, LightEntity, RestoreEntity):
    _attr_name = None
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = [EFFECT_STATIC, *EFFECTS]
    _attr_assumed_state = True

    def __init__(self, data, node) -> None:
        super().__init__(data, node)
        self._attr_unique_id = f"{data.mesh_id}-{self._address}-light"
        self._attr_is_on = None
        self._attr_brightness = 255
        self._attr_rgb_color = (255, 255, 255)
        self._attr_effect = EFFECT_STATIC

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._attr_is_on = last.state == "on" if last.state in ("on", "off") else None
            a = last.attributes
            self._attr_brightness = a.get(ATTR_BRIGHTNESS) or self._attr_brightness
            if a.get(ATTR_RGB_COLOR):
                self._attr_rgb_color = tuple(a[ATTR_RGB_COLOR])
            if a.get(ATTR_EFFECT) in self._attr_effect_list:
                self._attr_effect = a[ATTR_EFFECT]
        self.async_on_remove(self._data.mesh.add_listener(self._address, self._on_status))
        self._settings.listeners.append(self._on_settings_changed)
        self.async_on_remove(lambda: self._settings.listeners.remove(self._on_settings_changed))

    @callback
    def _on_status(self, on: bool) -> None:
        self._attr_is_on = on
        self.async_write_ha_state()

    async def _on_settings_changed(self) -> None:
        """Re-apply the running effect when speed or palette changes."""
        if self._attr_is_on and self._attr_effect != EFFECT_STATIC:
            await self._send(self._scene_msg())

    def _scene_msg(self) -> bytes:
        if self._attr_effect == EFFECT_STATIC or self._attr_effect not in EFFECTS:
            return vendor_scene(SCENE_STATIC, self._settings.speed, [self._attr_rgb_color])
        scene, kind = EFFECTS[self._attr_effect]
        if kind == "multi":
            colors = [hex_rgb(h) for h in THEMES.get(self._settings.palette, THEMES["Christmas"])]
        else:
            colors = [self._attr_rgb_color]
        return vendor_scene(scene, self._settings.speed, colors)

    async def _send(self, *msgs: bytes) -> None:
        try:
            await self._data.mesh.async_send(self._address, *msgs)
        except (BleakError, MeshError) as err:
            raise HomeAssistantError(f"Could not reach {self._node['n']}: {err}") from err

    async def _power(self, on: bool) -> None:
        """Vendor power (0x0211E0), acknowledged by 0x0211E3. Generic OnOff does NOT drive the LEDs."""
        try:
            reply = await self._data.mesh.async_request(self._address, vendor_power(on), POWER_STATUS_OP)
        except (BleakError, MeshError) as err:
            raise HomeAssistantError(
                f"{self._node['n']} did not respond ({err}). Download diagnostics for details."
            ) from err
        self._attr_is_on = bool(reply[0])

    async def async_turn_on(self, **kwargs: Any) -> None:
        msgs: list[bytes] = []
        if not self._attr_is_on or not kwargs:
            await self._power(True)
        if ATTR_RGB_COLOR in kwargs:
            self._attr_rgb_color = tuple(kwargs[ATTR_RGB_COLOR])
            if ATTR_EFFECT not in kwargs and self._attr_effect in EFFECTS and EFFECTS[self._attr_effect][1] == "multi":
                self._attr_effect = EFFECT_STATIC  # picking a colour leaves palette effects
        if ATTR_EFFECT in kwargs:
            self._attr_effect = kwargs[ATTR_EFFECT]
        if ATTR_RGB_COLOR in kwargs or ATTR_EFFECT in kwargs:
            msgs.append(self._scene_msg())
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
            msgs.append(vendor_brightness(_ha_to_index(self._attr_brightness)))
        if msgs:
            await self._send(*msgs)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._power(False)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        try:
            reply = await self._data.mesh.async_request(self._address, vendor_power_get(), POWER_STATUS_OP)
            self._attr_is_on = bool(reply[0])
        except (BleakError, MeshError) as err:
            _LOGGER.debug("refresh failed: %s", err)
