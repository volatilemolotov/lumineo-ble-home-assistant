"""Read-only check of device-key messaging: fetch Composition Data from the light."""
import asyncio
import sys

from bleak import BleakClient, BleakScanner

from meshlib import PROXY_IN, PROXY_OUT, Mesh  # also puts the integration dir on sys.path
from mesh import ProxyReassembler, config_composition_get, opcode_len, parse_composition

CFG = sys.argv[1] if len(sys.argv) > 1 else "mesh.json"


async def main():
    mesh = Mesh(CFG)
    node = mesh.nodes[0]
    dst, dev_key = node["a"], bytes.fromhex(node["k"])
    mesh.device_keys[dst] = dev_key
    client = None
    for attempt in range(8):
        dev = await BleakScanner.find_device_by_filter(lambda d, a: d.address.upper() == node["m"].upper(), timeout=20)
        if not dev:
            continue
        try:
            client = BleakClient(dev, timeout=30)
            await client.connect()
            break
        except Exception as e:
            print(f"connect attempt {attempt} failed: {e}")
            client = None
    if not client:
        raise SystemExit("could not connect")
    got = asyncio.Event()
    rx = ProxyReassembler()
    try:
        def on_notify(_, data):
            pdu = rx.feed(bytes(data))
            if not pdu:
                return
            msgs, out = mesh.handle_pdu(pdu)
            for o in out:
                asyncio.get_running_loop().create_task(client.write_gatt_char(PROXY_IN, o, response=False))
                print("  -> segment ack")
            for src, acc in msgs:
                n = opcode_len(acc)
                print(f"  <- {src:#06x} op={acc[:n].hex()} len={len(acc)}")
                if acc[:n] == b"\x02":
                    print("  composition:", parse_composition(acc[n:]))
                    got.set()

        await client.start_notify(PROXY_OUT, on_notify)
        await asyncio.sleep(1)
        await client.write_gatt_char(PROXY_IN, mesh.proxy_add_filter([mesh.src]), response=False)
        await asyncio.sleep(0.3)
        for pdu in mesh.access_pdus(dst, config_composition_get(), dev_key=dev_key):
            await client.write_gatt_char(PROXY_IN, pdu, response=False)
        print("-> Composition Data Get (device key)")
        await asyncio.wait_for(got.wait(), 10)
        await asyncio.sleep(1)
    finally:
        await client.disconnect()


asyncio.run(main())
