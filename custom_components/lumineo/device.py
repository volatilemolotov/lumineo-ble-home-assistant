"""BLE connections to Lumineo mesh nodes (works through ESPHome Bluetooth proxies)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback

from .const import IDLE_DISCONNECT
from .mesh import (
    CONFIG_MODELS,
    OP_APPKEY_STATUS,
    OP_COMPOSITION_STATUS,
    OP_MODEL_APP_STATUS,
    OP_NODE_RESET_STATUS,
    PROXY_IN,
    PROXY_OUT,
    MeshCrypto,
    ProxyReassembler,
    config_appkey_add,
    config_composition_get,
    config_model_app_bind,
    config_node_reset,
    opcode_len,
    parse_composition,
    parse_status,
    proxy_sar_split,
    vendor_power_get,
)

_LOGGER = logging.getLogger(__name__)


class MeshError(Exception):
    pass


class MeshLink:
    """A GATT proxy connection to one node, with request/response helpers."""

    def __init__(self, crypto: MeshCrypto, on_access: Callable[[int, bytes], None] | None = None) -> None:
        self.crypto = crypto
        self._on_access = on_access
        self._client: BleakClientWithServiceCache | None = None
        self._rx = ProxyReassembler()
        self._waiters: list[tuple[int, bytes, asyncio.Future]] = []
        self._tasks: set[asyncio.Task] = set()
        self.history: deque[tuple[float, str, str]] = deque(maxlen=150)
        self.services: list[str] = []

    def _log(self, kind: str, msg: str) -> None:
        self.history.append((round(time.time(), 3), kind, msg))
        _LOGGER.debug("%s %s", kind, msg)

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def connect(self, device: BLEDevice, fresh: bool = True) -> None:
        # Never trust a cached GATT table: pairing turns the 0x1827 service into 0x1828.
        source = device.details.get("source") if isinstance(device.details, dict) else None
        self._log("conn", f"connecting to {device.address} via {source or 'local adapter'}")
        client = await establish_connection(
            BleakClientWithServiceCache, device, device.name or device.address,
            disconnected_callback=self._on_disconnect,
            max_attempts=4, use_services_cache=not fresh,
        )
        self.services = [
            f"{svc.uuid} [{', '.join(f'{c.uuid[4:8]}@{c.handle}:{'/'.join(c.properties)}' for c in svc.characteristics)}]"
            for svc in client.services
        ]
        self._log("conn", f"connected, mtu={client.mtu_size}, services={len(self.services)}")
        if not client.services.get_characteristic(PROXY_IN):
            await client.disconnect()
            raise MeshError("light has no Mesh Proxy characteristic (still unprovisioned, or stale GATT cache)")
        self._client = client
        await client.start_notify(PROXY_OUT, self._on_notify)
        await self.write(self.crypto.proxy_add_filter([self.crypto.src]))
        await asyncio.sleep(0.2)

    def _on_disconnect(self, _client) -> None:
        self._log("conn", "disconnected")
        self._client = None

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except BleakError:
                pass
            self._client = None

    async def write(self, pdu: bytes) -> None:
        if not self._client:
            raise BleakError("not connected")
        max_len = max(20, (self._client.mtu_size or 23) - 3)
        frags = proxy_sar_split(pdu, max_len)
        self._log("tx", f"{pdu.hex()} ({len(frags)} frag, max {max_len})")
        for frag in frags:
            await self._client.write_gatt_char(PROXY_IN, frag, response=False)

    def _on_notify(self, _char, data: bytearray) -> None:
        self._log("rx-raw", bytes(data).hex())
        pdu = self._rx.feed(bytes(data))
        if not pdu:
            return
        msgs, replies = self.crypto.handle_pdu(pdu)
        for r in replies:
            t = asyncio.get_running_loop().create_task(self._safe_write(r))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        if not msgs and pdu[0] & 0x3F == 0x01:
            self._log("rx", f"beacon {pdu.hex()} (ours={pdu[3:11] == self.crypto.network_id})")
        for src, acc in msgs:
            self._log("rx", f"from {src:#06x}: {acc.hex()}")
            op = acc[:opcode_len(acc)]
            for w in list(self._waiters):
                if w[0] == src and w[1] == op and not w[2].done():
                    w[2].set_result(acc[len(op):])
            if self._on_access:
                self._on_access(src, acc)

    async def _safe_write(self, pdu: bytes) -> None:
        try:
            await self.write(pdu)
        except BleakError as err:
            _LOGGER.debug("ack write failed: %s", err)

    async def send(self, dst: int, msg: bytes, dev_key: bytes | None = None) -> None:
        self._log("send", f"to {dst:#06x} {'devkey' if dev_key else 'appkey'} {msg.hex()}")
        for pdu in self.crypto.access_pdus(dst, msg, dev_key=dev_key):
            await self.write(pdu)
            await asyncio.sleep(0.03)

    async def request(self, dst: int, msg: bytes, resp_op: bytes, dev_key: bytes | None = None,
                      timeout: float = 4.0, retries: int = 3) -> bytes:
        """Send `msg` and wait for a reply with opcode `resp_op` from `dst`; returns reply params."""
        for attempt in range(retries):
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            waiter = (dst, resp_op, fut)
            self._waiters.append(waiter)
            try:
                await self.send(dst, msg, dev_key)
                return await asyncio.wait_for(fut, timeout)
            except TimeoutError:
                _LOGGER.debug("no reply %s from %#06x (attempt %d)", resp_op.hex(), dst, attempt + 1)
            finally:
                self._waiters.remove(waiter)
        raise MeshError(f"no reply from {dst:#06x}")

    # ---- configuration (device key) -------------------------------------
    async def configure_node(self, addr: int, dev_key: bytes, app_key: bytes) -> dict:
        """Add the app key and bind it to every model on every element. Returns composition data."""
        comp = parse_composition(await self.request(addr, config_composition_get(), OP_COMPOSITION_STATUS, dev_key))
        status = await self.request(addr, config_appkey_add(app_key), OP_APPKEY_STATUS, dev_key)
        if status[0] not in (0x00, 0x06):  # success / key already stored
            raise MeshError(f"AppKey Add failed with status {status[0]:#04x}")
        for idx, element in enumerate(comp["elements"]):
            elem_addr = addr + idx
            binds = [(m, None) for m in element["sig"] if m not in CONFIG_MODELS]
            binds += [(mid, cid) for cid, mid in element["vendor"]]
            for model, cid in binds:
                try:
                    st = await self.request(addr, config_model_app_bind(elem_addr, model, cid), OP_MODEL_APP_STATUS, dev_key, retries=2)
                    if st[0]:
                        _LOGGER.debug("bind %s%04x: status %#04x", f"{cid:04x}:" if cid is not None else "", model, st[0])
                except MeshError:
                    _LOGGER.debug("bind %04x: no reply", model)
        return comp

    async def reset_node(self, addr: int, dev_key: bytes) -> None:
        await self.request(addr, config_node_reset(), OP_NODE_RESET_STATUS, dev_key)


class LumineoMesh:
    """One mesh network; connects on demand to whichever of its nodes is reachable."""

    def __init__(self, hass: HomeAssistant, crypto: MeshCrypto, nodes: list[dict]) -> None:
        self.hass = hass
        self.crypto = crypto
        self.nodes = nodes
        self._link = MeshLink(crypto, self._on_access)
        self._lock = asyncio.Lock()
        self._idle_handle: asyncio.TimerHandle | None = None
        self._listeners: dict[int, list[Callable[[bool], None]]] = {}

    @callback
    def add_listener(self, address: int, cb: Callable[[bool], None]) -> Callable[[], None]:
        self._listeners.setdefault(address, []).append(cb)
        return lambda: self._listeners[address].remove(cb)

    def _on_access(self, src: int, access: bytes) -> None:
        state = parse_status(access)
        if state is not None:
            for cb in list(self._listeners.get(src, [])):
                cb(state)

    def _find_device(self, prefer: int | None = None) -> BLEDevice | None:
        nodes = sorted(self.nodes, key=lambda n: n["a"] != prefer)
        for node in nodes:
            dev = bluetooth.async_ble_device_from_address(self.hass, node["m"].upper(), connectable=True)
            if dev:
                return dev
        return None

    async def _ensure_connected(self, prefer: int | None = None) -> MeshLink:
        if not self._link.connected:
            dev = self._find_device(prefer)
            if not dev:
                raise BleakError("no Lumineo light is currently reachable by any Bluetooth adapter/proxy")
            await self._link.connect(dev)
        return self._link

    def _schedule_idle_disconnect(self) -> None:
        if self._idle_handle:
            self._idle_handle.cancel()
        self._idle_handle = self.hass.loop.call_later(
            IDLE_DISCONNECT, lambda: self.hass.async_create_task(self.async_disconnect())
        )

    async def async_disconnect(self) -> None:
        async with self._lock:
            await self._link.disconnect()

    async def async_send(self, dst: int, *messages: bytes) -> None:
        async with self._lock:
            for attempt in range(2):
                try:
                    link = await self._ensure_connected(dst)
                    for msg in messages:
                        await link.send(dst, msg)
                        await asyncio.sleep(0.08)
                    break
                except BleakError as err:
                    await self._link.disconnect()
                    if attempt:
                        raise
                    _LOGGER.debug("send failed (%s), reconnecting", err)
            self._schedule_idle_disconnect()

    @property
    def link(self) -> MeshLink:
        return self._link

    async def async_request(self, dst: int, msg: bytes, resp_op: bytes) -> bytes:
        async with self._lock:
            for attempt in range(2):
                try:
                    link = await self._ensure_connected(dst)
                    return await link.request(dst, msg, resp_op, timeout=3, retries=2)
                except BleakError as err:
                    await self._link.disconnect()
                    if attempt:
                        raise
                    _LOGGER.debug("request failed (%s), reconnecting", err)
                finally:
                    self._schedule_idle_disconnect()
        raise MeshError("unreachable")

    async def async_refresh(self, dst: int) -> None:
        await self.async_send(dst, vendor_power_get())

    async def async_reset_node(self, addr: int, dev_key: bytes) -> None:
        async with self._lock:
            link = await self._ensure_connected(addr)
            await link.reset_node(addr, dev_key)
            await link.disconnect()  # the node drops off the network now
