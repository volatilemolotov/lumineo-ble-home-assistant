"""End-to-end pairing test from this machine (BlueZ), mirroring what the HA integration does.

    python3 tools/pair.py mesh.json --reset   # unpair the light from the app mesh first (uses its device key)
    python3 tools/pair.py mesh.json           # light already unpaired / factory reset

Creates a brand-new mesh, provisions the first light from mesh.json into it,
adds the app key, binds models, blinks it, and writes mesh_ha.json (importable in HA
via "Import lights from the Lumineo app" -> paste the JSON).
"""
import asyncio
import json
import secrets
import sys

from bleak import BleakClient, BleakScanner

from meshlib import PROXY_IN, PROXY_OUT, Mesh, MeshCrypto, key_from_string, vendor_power  # sets sys.path
from mesh import (
    CONFIG_MODELS, OP_APPKEY_STATUS, OP_COMPOSITION_STATUS, OP_MODEL_APP_STATUS, OP_NODE_RESET_STATUS,
    ProxyReassembler, config_appkey_add, config_composition_get, config_model_app_bind, config_node_reset,
    opcode_len, parse_composition,
)
from provision import PROV_SERVICE, Provisioner

PROV_UUID = PROV_SERVICE.lower()


async def connect(addr, want_service=None, tries=10):
    for attempt in range(tries):
        dev = await BleakScanner.find_device_by_filter(
            lambda d, a: d.address.upper() == addr.upper() and (want_service is None or want_service in a.service_uuids),
            timeout=20,
        )
        if not dev:
            print("  not seen yet...")
            continue
        try:
            c = BleakClient(dev, timeout=30)
            await c.connect()
            return c
        except Exception as e:
            print(f"  connect attempt {attempt} failed: {e}")
    raise SystemExit(f"could not connect to {addr}")


class Link:
    def __init__(self, client, crypto):
        self.c, self.crypto, self.rx, self.waiters = client, crypto, ProxyReassembler(), []

    async def start(self):
        await self.c.start_notify(PROXY_OUT, self._notify)
        await asyncio.sleep(0.5)
        await self.c.write_gatt_char(PROXY_IN, self.crypto.proxy_add_filter([self.crypto.src]), response=False)
        await asyncio.sleep(0.3)

    def _notify(self, _, data):
        pdu = self.rx.feed(bytes(data))
        if not pdu:
            return
        msgs, out = self.crypto.handle_pdu(pdu)
        for o in out:
            asyncio.get_running_loop().create_task(self.c.write_gatt_char(PROXY_IN, o, response=False))
        for src, acc in msgs:
            op = acc[:opcode_len(acc)]
            for w in self.waiters:
                if w[0] == src and w[1] == op and not w[2].done():
                    w[2].set_result(acc[len(op):])

    async def send(self, dst, msg, dev_key=None):
        for pdu in self.crypto.access_pdus(dst, msg, dev_key=dev_key):
            await self.c.write_gatt_char(PROXY_IN, pdu, response=False)
            await asyncio.sleep(0.03)

    async def request(self, dst, msg, op, dev_key=None, retries=3):
        for _ in range(retries):
            fut = asyncio.get_running_loop().create_future()
            w = (dst, op, fut)
            self.waiters.append(w)
            try:
                await self.send(dst, msg, dev_key)
                return await asyncio.wait_for(fut, 4)
            except TimeoutError:
                pass
            finally:
                self.waiters.remove(w)
        raise RuntimeError(f"no reply {op.hex()} from {dst:#06x}")


async def main():
    cfg_path = sys.argv[1]
    old = Mesh(cfg_path)
    node = old.nodes[0]
    mac = node["m"]

    if "--reset" in sys.argv:
        print(f"1) Unpairing {mac} from the app mesh (Config Node Reset)")
        old.device_keys[node["a"]] = bytes.fromhex(node["k"])
        c = await connect(mac)
        link = Link(c, old)
        await link.start()
        await link.request(node["a"], config_node_reset(), OP_NODE_RESET_STATUS, bytes.fromhex(node["k"]))
        print("   reset acknowledged")
        await c.disconnect()
        await asyncio.sleep(5)

    net_pwd = secrets.token_hex(8).upper()
    crypto = MeshCrypto(key_from_string(net_pwd), key_from_string("LIGHTSTRING12345"), 0x00F0, old._next_seq)  # shares seq.json
    unicast = 0x0001

    print(f"2) Provisioning {mac} into new mesh (net key {net_pwd}) as {unicast:#06x}")
    c = await connect(mac, want_service=PROV_UUID)
    try:
        dev_key, elements = await Provisioner(c).run(crypto.net_key, unicast)
    finally:
        await c.disconnect()
    print(f"   provisioned: device key {dev_key.hex()}, {elements} element(s)")
    crypto.device_keys[unicast] = dev_key

    result = {"meshPwd": net_pwd, "meshName": "LIGHTSTRING12345", "ivIndex": 0,
              "nodes": [{"n": node.get("n", "Light String 1"), "m": mac, "a": unicast, "k": dev_key.hex().upper(), "e": elements}]}
    json.dump(result, open("mesh_ha.json", "w"), indent=2)
    print("   saved mesh_ha.json")

    print("3) Reconnecting as proxy and configuring")
    await asyncio.sleep(3)
    c = await connect(mac)
    link = Link(c, crypto)
    await link.start()
    comp = parse_composition(await link.request(unicast, config_composition_get(), OP_COMPOSITION_STATUS, dev_key))
    print("   composition:", comp)
    st = await link.request(unicast, config_appkey_add(crypto.app_key), OP_APPKEY_STATUS, dev_key)
    print(f"   AppKey Add status {st[0]:#04x}")
    for idx, el in enumerate(comp["elements"]):
        for model, cid in [(m, None) for m in el["sig"] if m not in CONFIG_MODELS] + [(m, c_) for c_, m in el["vendor"]]:
            try:
                st = await link.request(unicast, config_model_app_bind(unicast + idx, model, cid), OP_MODEL_APP_STATUS, dev_key, retries=2)
                print(f"   bind {model:04x}{' vendor' if cid is not None else ''}: status {st[0]:#04x}")
            except RuntimeError as e:
                print(f"   bind {model:04x}: {e}")

    print("4) Blink: off, on")
    await link.send(unicast, vendor_power(False))
    await asyncio.sleep(2)
    await link.send(unicast, vendor_power(True))
    await asyncio.sleep(1)
    await c.disconnect()
    print("done — the light now belongs to the new mesh in mesh_ha.json")


if __name__ == "__main__":
    asyncio.run(main())
