"""Exercise vendor commands: static colours, brightness, and a multi-colour effect."""
import asyncio
import sys

from bleak import BleakClient, BleakScanner

from meshlib import PROXY_IN, PROXY_OUT, THEMES, Mesh, hex_rgb, vendor_brightness, vendor_power, vendor_scene

CFG = sys.argv[1] if len(sys.argv) > 1 else "mesh.json"


async def connect(addr):
    for attempt in range(8):
        dev = await BleakScanner.find_device_by_filter(lambda d, a: d.address.upper() == addr.upper(), timeout=20)
        if not dev:
            continue
        try:
            c = BleakClient(dev, timeout=30)
            await c.connect()
            return c
        except Exception as e:
            print(f"connect attempt {attempt} failed: {e}")
    raise SystemExit("could not connect")


async def main():
    mesh = Mesh(CFG)
    node = mesh.nodes[0]
    dst = node["a"]
    c = await connect(node["m"])
    try:
        def on_notify(_, data):
            data = bytes(data)
            if data[0] & 0x3F == 0:
                info = mesh.decrypt_network(data[1:])
                if info:
                    acc = mesh.decrypt_access(info)
                    print(f"  <- {info['src']:#06x}: {acc.hex() if acc else 'ctl/seg ' + info['transport'].hex()}")

        await c.start_notify(PROXY_OUT, on_notify)
        await asyncio.sleep(1)
        await c.write_gatt_char(PROXY_IN, mesh.proxy_add_filter([mesh.src]), response=False)

        async def send(label, access, wait=3):
            print("->", label, access.hex())
            for pdu in mesh.access_pdus(dst, access):
                await c.write_gatt_char(PROXY_IN, pdu, response=False)
                await asyncio.sleep(0.05)
            await asyncio.sleep(wait)

        await send("power on (vendor E0)", vendor_power(True), 1)
        await send("static RED", vendor_scene(0, 6, [(255, 0, 0)]))
        await send("static GREEN", vendor_scene(0, 6, [(0, 255, 0)]))
        await send("static BLUE", vendor_scene(0, 6, [(0, 0, 255)]))
        await send("static WARM WHITE", vendor_scene(0, 6, [(255, 180, 100)]))
        await send("brightness 42 (min)", vendor_brightness(42))
        await send("brightness 100 (max)", vendor_brightness(100))
        await send("effect Fireworks (multi, segmented) RAINBOW", vendor_scene(29, 6, [hex_rgb(h) for h in THEMES["Rainbow"]]), 6)
    finally:
        await c.disconnect()


asyncio.run(main())
