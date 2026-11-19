# Changelog

## 0.1.0 (first public release)

- Pair lights directly with Home Assistant, with Bluetooth discovery. No app needed.
- Import lights from the Lumineo app's share link or JSON.
- Light entity with on/off, RGB colour, brightness and 25 effects, plus Effect speed and Effect palette entities.
- Unpair (factory reset) button.
- Power uses the vendor `E0`/`E1` messages. The light acknowledges Generic OnOff, but the LEDs ignore it.
- Diagnostics no longer include mesh keys or device keys.
- Brand icon shipped with the integration (Home Assistant 2026.3+).
