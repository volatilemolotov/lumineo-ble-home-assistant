"""Config flow: import the Lumineo app's mesh, or pair lights directly with Home Assistant."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectOptionDict, SelectSelector, SelectSelectorConfig

from .const import CONF_MESH, CONF_OWNED, CONF_SHARE, DOMAIN, MESH_PROV_UUID
from .mesh import k3, key_from_string
from .pairing import PairingError, async_pair_light, owned_entry

_LOGGER = logging.getLogger(__name__)
CONF_ADDRESS = "address"
CONF_NAME = "name"


async def _load_share(hass, text: str) -> dict:
    text = text.strip()
    if text.startswith("{") and '"ipfsLink"' in text:
        text = json.loads(text)["ipfsLink"]
    if text.startswith("http"):
        async with async_get_clientsession(hass).get(text, timeout=20) as resp:
            resp.raise_for_status()
            return json.loads(await resp.text())
    return json.loads(text)


TELINK_CID = b"\x11\x02"  # first bytes of the device UUID in Telink's unprovisioned beacon


def _is_lumineo_candidate(info: BluetoothServiceInfoBleak) -> bool:
    data = info.service_data.get(MESH_PROV_UUID, b"")
    return MESH_PROV_UUID in info.service_uuids and data[:2] == TELINK_CID


def _unprovisioned(hass) -> dict[str, BluetoothServiceInfoBleak]:
    return {
        info.address: info
        for info in bluetooth.async_discovered_service_info(hass, connectable=True)
        if _is_lumineo_candidate(info)
    }


class LumineoConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str | None = None
        self._pair_task: asyncio.Task | None = None
        self._pair_result: tuple | None = None
        self._pair_error: str | None = None

    # ---- entry points -----------------------------------------------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["pair", "share"])

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        if not _is_lumineo_candidate(discovery_info):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(f"unprovisioned-{discovery_info.address}")
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self.context["title_placeholders"] = {"name": discovery_info.name or discovery_info.address}
        return await self.async_step_pair_confirm()

    # ---- import from the Lumineo app ----------------------------------------
    async def async_step_share(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                cfg = await _load_share(self.hass, user_input[CONF_SHARE])
                net_key = key_from_string(cfg["meshPwd"])
                key_from_string(cfg["meshName"])
                nodes = [
                    {"n": n.get("n") or f"Light {n['a']}", "m": n["m"], "a": int(n["a"]), "k": n.get("k"), "e": n.get("e", 1)}
                    for n in cfg["nodes"]
                ]
                if not nodes:
                    raise ValueError("no lights in share")
            except (ValueError, KeyError, TypeError):
                errors["base"] = "invalid_share"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("failed to load share")
                errors["base"] = "cannot_fetch"
            else:
                mesh = {"meshPwd": cfg["meshPwd"], "meshName": cfg["meshName"], "ivIndex": cfg.get("ivIndex", 0), "nodes": nodes}
                await self.async_set_unique_id(k3(net_key).hex())
                self._abort_if_unique_id_configured(updates={CONF_MESH: mesh})
                names = ", ".join(n["n"] for n in nodes)
                return self.async_create_entry(title=f"Lumineo app mesh ({names})", data={CONF_MESH: mesh})
        return self.async_show_form(step_id="share", data_schema=vol.Schema({vol.Required(CONF_SHARE): str}), errors=errors)

    # ---- pair with Home Assistant -----------------------------------------
    def _default_name(self) -> str:
        entry = owned_entry(self.hass)
        return f"Light String {len(entry.data[CONF_MESH]['nodes']) + 1 if entry else 1}"

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        found = _unprovisioned(self.hass)
        if user_input is not None:
            self._address = user_input[CONF_ADDRESS]
            self._name = user_input[CONF_NAME]
            return await self.async_step_provision()
        if not found:
            return self.async_show_form(step_id="pair", data_schema=vol.Schema({}), errors={"base": "no_devices"})
        options = [
            SelectOptionDict(value=a, label=f"{i.name or 'Unnamed'} ({a}, {i.rssi} dBm)")
            for a, i in sorted(found.items(), key=lambda kv: -kv[1].rssi)
        ]
        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): SelectSelector(SelectSelectorConfig(options=options)),
                vol.Required(CONF_NAME, default=self._default_name()): str,
            }),
        )

    async def async_step_pair_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._name = user_input[CONF_NAME]
            return await self.async_step_provision()
        return self.async_show_form(
            step_id="pair_confirm",
            data_schema=vol.Schema({vol.Required(CONF_NAME, default=self._default_name()): str}),
            description_placeholders={"address": self._address or ""},
        )

    async def async_step_provision(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._pair_task is None:
            self._pair_task = self.hass.async_create_task(async_pair_light(self.hass, self._address, self._name))
        if not self._pair_task.done():
            return self.async_show_progress(step_id="provision", progress_action="pairing", progress_task=self._pair_task)
        try:
            self._pair_result = self._pair_task.result()
        except PairingError as err:
            self._pair_error = str(err)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("pairing failed")
            self._pair_error = f"unexpected error: {err}"
        return self.async_show_progress_done(next_step_id="pair_failed" if self._pair_error else "pair_done")

    async def async_step_pair_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason="pair_failed", description_placeholders={"error": self._pair_error or ""})

    async def _drop_from_other_entries(self, mac: str, keep_entry_id: str | None) -> None:
        """Pairing reset the light, so any other mesh (e.g. the imported app mesh) has lost it."""
        for other in self.hass.config_entries.async_entries(DOMAIN):
            if other.entry_id == keep_entry_id:
                continue
            mesh = dict(other.data.get(CONF_MESH, {}))
            nodes = [n for n in mesh.get("nodes", []) if n["m"].upper() != mac.upper()]
            if len(nodes) == len(mesh.get("nodes", [])):
                continue
            if not nodes:
                _LOGGER.info("removing %s: its only light was paired with Home Assistant", other.title)
                await self.hass.config_entries.async_remove(other.entry_id)
            else:
                mesh["nodes"] = nodes
                self.hass.config_entries.async_update_entry(other, data={**other.data, CONF_MESH: mesh})
                await self.hass.config_entries.async_reload(other.entry_id)

    async def async_step_pair_done(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry, cfg = self._pair_result
        await self._drop_from_other_entries(self._address or "", entry.entry_id if entry else None)
        if entry:
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_MESH: cfg})
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason="light_added", description_placeholders={"name": self._name or ""})
        await self.async_set_unique_id(k3(key_from_string(cfg["meshPwd"])).hex(), raise_on_progress=False)
        return self.async_create_entry(title="Lumineo (Home Assistant mesh)", data={CONF_MESH: cfg, CONF_OWNED: True})
