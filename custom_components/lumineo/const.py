"""Constants for the Lumineo integration."""
DOMAIN = "lumineo"

CONF_SHARE = "share"          # share link (from the app's QR code) or raw JSON
CONF_MESH = "mesh"            # mesh settings: meshPwd, meshName, ivIndex, nodes
CONF_SRC = "src_address"
CONF_OWNED = "owned"          # True: mesh created by Home Assistant (lights paired by HA)

# Unicast address Home Assistant uses on the mesh. Must differ from the official
# app (random high address) and from any other controller you run (test scripts use 0x00F0).
DEFAULT_SRC = 0x00F1

# Lights paired by Home Assistant get addresses starting here.
FIRST_NODE_ADDRESS = 0x0001

# Disconnect after this many seconds idle so the official app can still connect.
IDLE_DISCONNECT = 60

# On startup, skip ahead this far in the sequence space in case the last saves were lost.
SEQ_STARTUP_JUMP = 256

APP_KEY_DEFAULT = "LIGHTSTRING12345"
MESH_PROV_UUID = "00001827-0000-1000-8000-00805f9b34fb"
