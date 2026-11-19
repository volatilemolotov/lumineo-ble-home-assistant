"""Connect to the Lumineo proxy, query power, and toggle the light off -> on (vendor E1/E0)."""
import asyncio
import sys

from bleak import BleakClient, BleakScanner

from meshlib import PROXY_IN, PROXY_OUT, Mesh, vendor_power, vendor_power_get

CFG = sys.argv[1] if len(sys.argv) > 1 else "mesh.json"


async def main():
    mesh = Mesh(CFG)
    node = mesh.nodes[0]
    addr, dst = node["m"], node["a"]
    print(f"netID={mesh.network_id.hex()} NID={mesh.nid:#x} AID={mesh.aid:#x} seq={mesh.seq}")

    client = None
    for attempt in range(8):
        dev = await BleakScanner.find_device_by_filter(lambda d, a: d.address.upper() == addr.upper(), timeout=20)
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
        print("could not connect")
        return

    c = client
    try:
        print("connected, mtu", c.mtu_size)

        def on_notify(_, data: bytearray):
            data = bytes(data)
            kind = data[0] & 0x3F
            if kind == 0:
                info = mesh.decrypt_network(data[1:])
                if info:
                    acc = mesh.decrypt_access(info)
                    print(f"  <- from {info['src']:#06x} to {info['dst']:#06x}: {acc.hex() if acc else info['transport'].hex()}")
            elif kind == 2:
                info = mesh.decrypt_proxy_config(data[1:])
                print("  <- proxy config", info["transport"].hex() if info else data.hex())
            elif kind == 1:
                print("  <- beacon", data.hex())

        await c.start_notify(PROXY_OUT, on_notify)
        await asyncio.sleep(1)
        await c.write_gatt_char(PROXY_IN, mesh.proxy_add_filter([mesh.src]), response=False)
        await asyncio.sleep(0.5)

        print("-> power status (E1)")
        await c.write_gatt_char(PROXY_IN, mesh.access_pdu(dst, vendor_power_get()), response=False)
        await asyncio.sleep(1.5)
        print("-> OFF")
        await c.write_gatt_char(PROXY_IN, mesh.access_pdu(dst, vendor_power(False)), response=False)
        await asyncio.sleep(3)
        print("-> ON")
        await c.write_gatt_char(PROXY_IN, mesh.access_pdu(dst, vendor_power(True)), response=False)
        await asyncio.sleep(2)
    finally:
        await c.disconnect()


asyncio.run(main())
