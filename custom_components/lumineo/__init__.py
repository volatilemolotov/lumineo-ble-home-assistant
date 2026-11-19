"""Lumineo (Kaemingk Dancing Lights) Bluetooth mesh lights."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import CONF_MESH, CONF_SRC, DEFAULT_SRC, DOMAIN, SEQ_STARTUP_JUMP
from .device import LumineoMesh
from .mesh import MeshCrypto, k3, key_from_string

PLATFORMS = [Platform.BUTTON, Platform.LIGHT, Platform.NUMBER, Platform.SELECT]


@dataclass
class LumineoData:
    mesh: LumineoMesh
    crypto: MeshCrypto
    nodes: list[dict]
    mesh_id: str


type LumineoConfigEntry = ConfigEntry[LumineoData]


async def async_build_crypto(hass: HomeAssistant, cfg: dict, src: int) -> tuple[MeshCrypto, Callable[[], Awaitable[None]]]:
    """MeshCrypto whose sequence numbers persist in HA storage (keyed by network, not entry)."""
    net_key = key_from_string(cfg["meshPwd"])
    store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{k3(net_key).hex()}.{src:04x}.seq")
    saved = await store.async_load() or {}
    state = {"seq": int(saved.get("seq", 1)) + SEQ_STARTUP_JUMP}
    await store.async_save(state)

    def next_seq() -> int:
        s = state["seq"]
        state["seq"] = s + 1
        store.async_delay_save(lambda: dict(state), 2)
        return s

    crypto = MeshCrypto(net_key, key_from_string(cfg["meshName"]), src, next_seq, cfg.get("ivIndex", 0))
    for node in cfg["nodes"]:
        if node.get("k"):
            crypto.device_keys[node["a"]] = bytes.fromhex(node["k"])

    async def save() -> None:
        await store.async_save(dict(state))

    return crypto, save


async def async_setup_entry(hass: HomeAssistant, entry: LumineoConfigEntry) -> bool:
    cfg = entry.data[CONF_MESH]
    crypto, save = await async_build_crypto(hass, cfg, entry.data.get(CONF_SRC, DEFAULT_SRC))
    mesh = LumineoMesh(hass, crypto, cfg["nodes"])
    entry.runtime_data = LumineoData(mesh, crypto, cfg["nodes"], crypto.network_id.hex())

    async def _on_unload() -> None:
        await mesh.async_disconnect()
        await save()

    entry.async_on_unload(_on_unload)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LumineoConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
