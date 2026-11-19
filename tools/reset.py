"""Factory-reset (unpair) the first light in a mesh JSON via Config Node Reset (device key)."""
import asyncio
import sys

from pair import Link, connect  # also sets up sys.path via meshlib
from meshlib import Mesh
from mesh import OP_NODE_RESET_STATUS, config_node_reset


async def main():
    m = Mesh(sys.argv[1])
    node = m.nodes[0]
    key = bytes.fromhex(node["k"])
    m.device_keys[node["a"]] = key
    c = await connect(node["m"])
    link = Link(c, m)
    await link.start()
    await link.request(node["a"], config_node_reset(), OP_NODE_RESET_STATUS, key)
    print(f"{node['m']} reset acknowledged — it is now unpaired")
    await c.disconnect()


asyncio.run(main())
