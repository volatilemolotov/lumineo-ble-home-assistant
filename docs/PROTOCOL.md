# Lumineo Dancing Lights: protocol notes

These notes describe how the lights talk over Bluetooth, as observed while building this integration.
They are written for interoperability. Nothing here is official documentation from the manufacturer.

## Transport

The light controllers are **Bluetooth SIG Mesh** nodes built on Telink chips (company ID `0x0211`).
Nothing proprietary sits below the mesh layer: standard SIG Mesh 1.0 provisioning, network and
transport layers, reached through the GATT bearers.

| Bearer | Service | Data in | Data out | When |
|---|---|---|---|---|
| PB-GATT (provisioning) | `0x1827` | `0x2ADB` | `0x2ADC` | unprovisioned light |
| Mesh Proxy | `0x1828` | `0x2ADD` | `0x2ADE` | provisioned light |

Quirks:

- The proxy characteristics sit under a vendor service (`0x7FDD`) instead of `0x1828` in the GATT table.
  Look them up by characteristic UUID.
- The GATT table changes after provisioning, so never trust a cached one.
- A light accepts **one** BLE connection at a time.
- The Node Identity advertisement hash doesn't match the spec calculation. The Secure Network Beacon
  sent over the proxy connection does, so use that to check you have the right network key.

## Keys

The official app builds its keys from 16-character strings and uses their **raw ASCII bytes** as the
128-bit key. It doesn't hash them.

| Key | Value |
|---|---|
| App key | `LIGHTSTRING12345`, the same for every installation (AID `0x11`) |
| Network key | A random 16-character string generated per app installation (`meshPwd`) |
| IV index | `0` |

The app's "share settings" QR code points to a JSON file with the network key, the app key, and every
light's unicast address, MAC address and device key. Anyone with that file can control the lights.

When Home Assistant pairs a light itself, it creates a fresh random network key and keeps the same
app key, so the vendor messages keep working.

## Provisioning

Standard PB-GATT provisioning with the No OOB method and an ECDH P-256 key exchange. The unprovisioned
beacon's device UUID starts with `11 02` (the Telink company ID, little-endian), which is how the
integration recognises candidate lights. Attention timer values make the light blink while it pairs.

After provisioning, configure the node with its device key:

1. Config Composition Data Get (`80 08`) returns the elements and models.
2. Config AppKey Add (`00`) adds the app key.
3. Config Model App Bind (`80 3D`) binds every SIG and vendor model, except Configuration Server and Client.

Config Node Reset (`80 49`) unpairs a light. It then advertises as unprovisioned again.

## Vendor messages

The light is driven by vendor model messages with 3-byte opcodes `<op> 11 02`, sent with the app key.

| Opcode | Parameters | Reply | Meaning |
|---|---|---|---|
| `E0 11 02` | `on` (`00`/`01`) | `E3 11 02 on` | Power |
| `E1 11 02` | none | `E3 11 02 on` | Power status query |
| `E6 11 02` | `scene, speed, n, n × (00, r, g, b)` | none | Scene/effect with colours. Scene `00` is a static colour |
| `F3 11 02` | `level` | none | Brightness 0 to 255, 255 is full. The app sends `gamma[n]` with n from 42 up; the integration maps HA brightness to n = 42…255 |
| `F0 11 02` | | | "Find my light": flashes for a few seconds |

Important: the light acknowledges **Generic OnOff** (`82 02`/`82 03`), but the LEDs ignore it.
Power has to go through `E0`.

### Colours

The app corrects colours before sending them:

1. A gamma curve, the same 256-entry table used for brightness (see `GAMMA` in `mesh.py`).
2. A white balance of R × 1.0, G × 0.85, B × 0.4.
3. Pure blue (red and green both 0) is doubled, capped at 122, because the blue LEDs are dim.

Effect speed runs from 1 to 10. Multi-colour effects take up to five colours (the app's "themes").
The scene numbers the integration uses are listed in `EFFECTS` in `mesh.py`.

## State

The lights don't report colour, effect or brightness, so the integration assumes them.
Power is reported in `E3` replies, both to `E0`/`E1` and when the state changes while a controller is connected.
