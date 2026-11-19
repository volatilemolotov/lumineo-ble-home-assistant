<p align="center">
  <img src="custom_components/lumineo/brand/icon@2x.png" width="160" alt="">
</p>

# Lumineo Dancing Lights for Home Assistant

Local Bluetooth control of **Lumineo "Dancing Lights"** Christmas light strings
(sold by Kaemingk, app `com.kaemingk.dancinglights`) from Home Assistant.
No cloud and no phone app needed. It works through **ESPHome Bluetooth proxies**
or a Bluetooth adapter on the Home Assistant host.

> [!NOTE]
> This is an unofficial, community-made integration. It is not affiliated with, endorsed by or
> supported by Kaemingk or Lumineo. Product names are trademarks of their owners.

## Features

- **Pair lights directly with Home Assistant**, with no app account needed. Lights are discovered
  automatically when a Bluetooth proxy sees them.
- **Or import the lights from the Lumineo app**, so the app and Home Assistant work side by side.
- One light entity per string with **on/off, RGB colour, brightness and 25 effects**
  (Flash, Chase, Fade, Fireworks, Snow, Rainbow waterfall and more).
- **Effect speed** (1–10) and **Effect palette** (Christmas, Jingle Bells, Ice, Lavender, Flamingo, Rainbow) controls.
- An **Unpair (factory reset)** button.
- Diagnostics download with mesh traffic for troubleshooting. Keys are removed.

## Supported lights

| Product | Status |
|---|---|
| Lumineo Dancing Lights LED string (Bluetooth controller) | ✅ Tested |
| Other Lumineo / Kaemingk lights that use the "Dancing Lights" app | ❓ Probably work, please report |

The controllers are Bluetooth SIG Mesh devices on Telink chips. Other "smart" light strings that
use the same Telink vendor model may work too. Please open an issue with the results either way.

## Installation

Requires **Home Assistant 2026.3** or newer.

### Marketplace (HACS)

1. In Home Assistant, open **Settings → Marketplace**. On releases before 2026.11, open **HACS**.
2. **⋮ → Custom repositories**, add this repository's URL with the type **Integration**.
3. Search for **Lumineo Dancing Lights**, download it and restart Home Assistant.

### Manual

Copy `custom_components/lumineo` into your Home Assistant `config/custom_components/` folder
and restart Home Assistant.

## Setup

Go to **Settings → Devices & services → Add integration → Lumineo Dancing Lights**. Pick one of these:

### Option A: Pair a new light with Home Assistant (recommended)

1. Power the light near a Bluetooth proxy or adapter.
   The light must be **unpaired**: new, factory-reset, or unpaired from the app (see below).
2. Choose **Pair a new light with Home Assistant**, pick the light and give it a name.
   Home Assistant may also offer it under **Discovered** on its own.
3. The light blinks while it pairs. Pairing takes 10–30 seconds.

Home Assistant creates its own private mesh network with random keys. Later lights join the same network.
A light paired this way **no longer works with the Lumineo app**.

### Option B: Import lights from the Lumineo app

Use this to keep using the app as well.

1. In the Lumineo app, open the **share settings** QR code.
2. Decode the QR code with any QR scanner app. You get a link ending in `.json`.
3. Choose **Import lights from the Lumineo app** and paste the link, or the content of the JSON file.

Home Assistant joins the app's mesh as an extra controller. Nothing is re-paired.

> [!WARNING]
> The share link contains your lights' keys. Anyone with the link can control the lights,
> so don't post it anywhere.

### Moving a light from the app to Home Assistant

Import the app mesh (Option B), press the light's **Unpair (factory reset)** button, then pair it
with Home Assistant (Option A). Home Assistant removes the light from the imported entry on its own.

## Entities

| Entity | Description |
|---|---|
| `light.<name>` | On/off, colour, brightness and effect. **Static** shows the selected colour. Single-colour effects use the selected colour. Multi-colour effects use the palette |
| `number.<name>_effect_speed` | Effect speed, 1 (slow) to 10 (fast) |
| `select.<name>_effect_palette` | Colour palette for multi-colour effects |
| `button.<name>_unpair_factory_reset` | Factory-resets the light and removes it from Home Assistant |

## Tips and limitations

- **ESPHome proxies need active connections:** set `bluetooth_proxy: active: true`.
- **A light accepts only one Bluetooth connection at a time.** Home Assistant disconnects after 60 seconds
  idle so the phone app can connect. While the app is connected, Home Assistant can't reach the light.
- **The state is partly assumed.** The lights report on/off, but not colour, effect or brightness.
  If you change those with the app or the remote, Home Assistant won't know.
- **Commands take about a second** when Home Assistant first has to connect to the light.

## Troubleshooting

- **The light doesn't show up for pairing:** it is probably still paired with the app, or out of range of
  any proxy. Unpair or factory-reset it, and move a proxy closer.
- **"did not respond" errors:** check that no phone has the app open, and that a proxy is in range.
- **Report a problem:** open an issue and attach the diagnostics file
  (**Settings → Devices & services → Lumineo → ⋮ → Download diagnostics**). The file contains no keys.
  For logs, enable debug logging on the integration page, reproduce the problem and attach the log.

## How it works

The lights are standard Bluetooth SIG Mesh nodes with a few vendor-specific messages for colours and
effects. The integration includes a small, self-contained mesh stack in pure Python
(provisioning, network/transport encryption, segmentation, proxy protocol) on top of
Home Assistant's Bluetooth stack. See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the details.

## Development

```bash
pip install pytest ruff cryptography
pytest            # protocol tests, including Bluetooth Mesh specification sample data
ruff check .
```

`tools/` has standalone scripts that talk to a light directly from a Linux machine
(BlueZ + [bleak](https://github.com/hbldh/bleak)), which helps when investigating the protocol.
They read a mesh JSON file (the app's share file, or one written by `tools/pair.py`), use mesh address
`0x00F0`, and keep their sequence number in `seq.json` next to the mesh file.
Both files hold or depend on secrets and are git-ignored.

| Script | Purpose |
|---|---|
| `tools/onoff.py mesh.json` | Query power, switch off and back on |
| `tools/colors.py mesh.json` | Cycle colours, brightness and an effect |
| `tools/composition.py mesh.json` | Read Composition Data (read-only) |
| `tools/pair.py mesh.json [--reset]` | Provision a light into a new mesh, like the integration does |
| `tools/reset.py mesh.json` | Factory-reset (unpair) the first light in the file |

## Made with AI assistance

The reverse engineering of the protocol and most of the code in this repository were done with the help
of AI, using Anthropic's Claude in Claude Code. This covered analysing the official app's behaviour and
Bluetooth traffic, working out the mesh keys and vendor messages, and writing the integration and tests.
All behaviour was verified by a human against real lights, and the cryptography is checked
against the Bluetooth Mesh specification's sample data in the tests.
Please report anything that looks wrong.

The repository contains no code, assets or key material copied from the official app. It contains only the
facts needed for interoperability: message formats, scene numbers, palette colours and colour-correction values.

## License

[MIT](LICENSE)
