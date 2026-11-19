"""Pair (provision + configure) an unprovisioned light into Home Assistant's own mesh."""
from __future__ import annotations

import asyncio
import logging
import secrets

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from . import async_build_crypto
from .const import APP_KEY_DEFAULT, CONF_MESH, CONF_OWNED, CONF_SRC, DEFAULT_SRC, DOMAIN, FIRST_NODE_ADDRESS
from .device import MeshLink
from .mesh import key_from_string, vendor_power
from .provision import Provisioner, ProvisioningError

_LOGGER = logging.getLogger(__name__)


class PairingError(Exception):
    pass


def owned_entry(hass: HomeAssistant) -> ConfigEntry | None:
    return next((e for e in hass.config_entries.async_entries(DOMAIN) if e.data.get(CONF_OWNED)), None)


def configured_addresses(hass: HomeAssistant) -> set[str]:
    """MAC addresses of every light in any Lumineo entry (HA's own mesh or an imported app mesh)."""
    return {
        node["m"].upper()
        for entry in hass.config_entries.async_entries(DOMAIN)
        for node in entry.data.get(CONF_MESH, {}).get("nodes", [])
    }


def new_mesh_config() -> dict:
    # Same shape as the Lumineo app's keys (16 ASCII chars, used as raw bytes).
    return {"meshPwd": secrets.token_hex(8).upper(), "meshName": APP_KEY_DEFAULT, "ivIndex": 0, "nodes": []}


def allocate_address(nodes: list[dict], src: int) -> int:
    addr = FIRST_NODE_ADDRESS
    for n in sorted(nodes, key=lambda n: n["a"]):
        if n["a"] <= addr < n["a"] + n.get("e", 1):
            addr = n["a"] + n.get("e", 1)
    if addr <= src < addr + 8:  # keep clear of HA's own address
        addr = src + 1
    return addr


async def _device(hass: HomeAssistant, address: str, timeout: float):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        dev = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
        if dev:
            return dev
        await asyncio.sleep(1)
    raise PairingError(f"{address} is not reachable by any Bluetooth adapter/proxy")


async def async_pair_light(hass: HomeAssistant, address: str, name: str) -> tuple[ConfigEntry | None, dict]:
    """Provision `address` and add it to HA's mesh. Returns (existing entry or None, new mesh config)."""
    entry = owned_entry(hass)
    cfg = dict(entry.data[CONF_MESH]) if entry else new_mesh_config()
    src = entry.data.get(CONF_SRC, DEFAULT_SRC) if entry else DEFAULT_SRC
    if entry and entry.state is ConfigEntryState.LOADED:
        crypto, save = entry.runtime_data.crypto, None
    else:
        crypto, save = await async_build_crypto(hass, cfg, src)

    unicast = allocate_address(cfg["nodes"], src)
    _LOGGER.info("pairing %s as %#06x", address, unicast)

    # 1. Provision over PB-GATT
    dev = await _device(hass, address, 10)
    client = await establish_connection(BleakClientWithServiceCache, dev, dev.name or address,
                                        max_attempts=4, use_services_cache=False)
    try:
        dev_key, elements = await Provisioner(client).run(key_from_string(cfg["meshPwd"]), unicast, cfg.get("ivIndex", 0))
    except ProvisioningError as err:
        raise PairingError(str(err)) from err
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    crypto.device_keys[unicast] = dev_key

    # 2. Reconnect via the Mesh Proxy service and configure (app key + model bindings)
    await asyncio.sleep(3)
    link = MeshLink(crypto)
    last_err: Exception | None = None
    for _ in range(5):
        try:
            await link.connect(await _device(hass, address, 20), fresh=True)
            break
        except Exception as err:  # noqa: BLE001
            last_err = err
            await asyncio.sleep(3)
    else:
        raise PairingError(f"light was provisioned but did not come back as a proxy: {last_err}")
    try:
        comp = await link.configure_node(unicast, dev_key, key_from_string(cfg["meshName"]))
        await link.send(unicast, vendor_power(True))
    except Exception as err:  # noqa: BLE001
        raise PairingError(f"light was provisioned but configuring it failed: {err}") from err
    finally:
        await link.disconnect()
    if save:
        await save()

    node = {"n": name, "m": address.upper(), "a": unicast, "k": dev_key.hex().upper(),
            "e": elements, "pid": comp["pid"]}
    cfg["nodes"] = [n for n in cfg["nodes"] if n["m"].upper() != address.upper()] + [node]
    return entry, cfg
