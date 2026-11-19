"""Minimal Bluetooth SIG Mesh proxy protocol for Lumineo (Telink) lights.

Pure protocol code, no I/O: builds and parses GATT proxy PDUs.
Implements network layer, lower transport (unsegmented + segmented TX),
upper transport (app key) and proxy configuration (filter add).
"""
from __future__ import annotations

import struct
from collections.abc import Callable

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.cmac import CMAC

PROXY_SERVICE = "00001828-0000-1000-8000-00805f9b34fb"
PROXY_IN = "00002add-0000-1000-8000-00805f9b34fb"
PROXY_OUT = "00002ade-0000-1000-8000-00805f9b34fb"

PDU_NETWORK = 0x00
PDU_BEACON = 0x01
PDU_PROXY_CONFIG = 0x02


def cmac(key: bytes, msg: bytes) -> bytes:
    c = CMAC(algorithms.AES(key))
    c.update(msg)
    return c.finalize()


def aes_ecb(key: bytes, block: bytes) -> bytes:
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return e.update(block) + e.finalize()


def s1(m: bytes) -> bytes:
    return cmac(bytes(16), m)


def k2(n: bytes, p: bytes = b"\x00") -> tuple[int, bytes, bytes]:
    t = cmac(s1(b"smk2"), n)
    t1 = cmac(t, p + b"\x01")
    t2 = cmac(t, t1 + p + b"\x02")
    t3 = cmac(t, t2 + p + b"\x03")
    return t1[15] & 0x7F, t2, t3


def k3(n: bytes) -> bytes:
    return cmac(cmac(s1(b"smk3"), n), b"id64\x01")[8:]


def k4(n: bytes) -> int:
    return cmac(cmac(s1(b"smk4"), n), b"id6\x01")[15] & 0x3F


def key_from_string(s: str) -> bytes:
    """The Lumineo app hands its key strings to Telink's SDK, which uses the raw bytes."""
    b = s.encode()
    if len(b) != 16:
        raise ValueError("mesh keys must be 16 characters")
    return b


class MeshCrypto:
    """Encrypts/decrypts mesh PDUs. `next_seq` must return a fresh, persisted sequence number."""

    def __init__(self, net_key: bytes, app_key: bytes, src: int, next_seq: Callable[[], int], iv_index: int = 0):
        self.net_key = net_key
        self.app_key = app_key
        self.src = src
        self.iv_index = iv_index
        self.next_seq = next_seq
        self.nid, self.enc_key, self.priv_key = k2(net_key)
        self.network_id = k3(net_key)
        self.aid = k4(app_key)
        self.device_keys: dict[int, bytes] = {}
        self._rx_segs: dict[tuple[int, int], dict[int, bytes]] = {}
        self._rx_done: dict[tuple[int, int], bool] = {}

    # ---- network layer -------------------------------------------------
    def _iv(self) -> bytes:
        return struct.pack(">I", self.iv_index)

    def _network_pdu(self, ctl: int, ttl: int, seq: int, dst: int, transport: bytes, nonce_type: int = 0x00) -> bytes:
        iv = self._iv()
        ctl_ttl = (ctl << 7) | ttl
        seq_b = seq.to_bytes(3, "big")
        src_b = self.src.to_bytes(2, "big")
        nonce = bytes([nonce_type, ctl_ttl if nonce_type == 0 else 0]) + seq_b + src_b + b"\x00\x00" + iv
        mic_len = 8 if ctl else 4
        enc = AESCCM(self.enc_key, tag_length=mic_len).encrypt(nonce, dst.to_bytes(2, "big") + transport, None)
        pecb = aes_ecb(self.priv_key, b"\x00" * 5 + iv + enc[:7])
        header = bytes(a ^ b for a, b in zip(bytes([ctl_ttl]) + seq_b + src_b, pecb[:6], strict=False))
        return bytes([((self.iv_index & 1) << 7) | self.nid]) + header + enc

    def decrypt_network(self, pdu: bytes, nonce_type: int = 0x00) -> dict | None:
        if len(pdu) < 14 or (pdu[0] & 0x7F) != self.nid:
            return None
        iv = self._iv()
        enc = pdu[7:]
        pecb = aes_ecb(self.priv_key, b"\x00" * 5 + iv + enc[:7])
        hdr = bytes(a ^ b for a, b in zip(pdu[1:7], pecb[:6], strict=False))
        ctl = hdr[0] >> 7
        nonce = bytes([nonce_type, hdr[0] if nonce_type == 0 else 0]) + hdr[1:6] + b"\x00\x00" + iv
        try:
            plain = AESCCM(self.enc_key, tag_length=8 if ctl else 4).decrypt(nonce, enc, None)
        except Exception:  # noqa: BLE001 - wrong key / corrupted
            return None
        return {
            "ctl": ctl,
            "ttl": hdr[0] & 0x7F,
            "seq": int.from_bytes(hdr[1:4], "big"),
            "src": int.from_bytes(hdr[4:6], "big"),
            "dst": int.from_bytes(plain[:2], "big"),
            "transport": plain[2:],
        }

    # ---- access messages -----------------------------------------------
    def _upper_key(self, dev_key: bytes | None) -> tuple[bytes, int, int, int]:
        """(key, akf, aid, nonce_type) for app-key or device-key encryption."""
        if dev_key is not None:
            return dev_key, 0, 0, 0x02
        return self.app_key, 1, self.aid, 0x01

    def access_pdus(self, dst: int, access: bytes, ttl: int = 5, dev_key: bytes | None = None) -> list[bytes]:
        """Proxy PDUs (type Network) carrying an access message (app key, or device key if given)."""
        key, akf, aid, nonce_type = self._upper_key(dev_key)
        seq = self.next_seq()
        nonce = bytes([nonce_type, 0]) + seq.to_bytes(3, "big") + self.src.to_bytes(2, "big") + dst.to_bytes(2, "big") + self._iv()
        upper = AESCCM(key, tag_length=4).encrypt(nonce, access, None)
        if len(access) <= 11:
            lower = bytes([(akf << 6) | aid]) + upper
            return [bytes([PDU_NETWORK]) + self._network_pdu(0, ttl, seq, dst, lower)]
        chunks = [upper[i:i + 12] for i in range(0, len(upper), 12)]
        if len(chunks) > 32:
            raise ValueError("access message too long")
        seg_n = len(chunks) - 1
        seq_zero = seq & 0x1FFF
        out = []
        for seg_o, chunk in enumerate(chunks):
            hdr = bytes([
                0x80 | (akf << 6) | aid,
                seq_zero >> 6,
                ((seq_zero & 0x3F) << 2) | (seg_o >> 3),
                ((seg_o & 0x07) << 5) | seg_n,
            ])
            s = seq if seg_o == 0 else self.next_seq()
            out.append(bytes([PDU_NETWORK]) + self._network_pdu(0, ttl, s, dst, hdr + chunk))
        return out

    def _decrypt_upper(self, src: int, dst: int, seq: int, akf: int, aid: int, szmic: int, upper: bytes) -> bytes | None:
        if akf:
            if aid != self.aid:
                return None
            key, nonce_type = self.app_key, 0x01
        else:
            key = self.device_keys.get(src)
            if key is None:
                return None
            nonce_type = 0x02
        nonce = bytes([nonce_type, szmic << 7]) + seq.to_bytes(3, "big") + src.to_bytes(2, "big") + dst.to_bytes(2, "big") + self._iv()
        try:
            return AESCCM(key, tag_length=8 if szmic else 4).decrypt(nonce, upper, None)
        except Exception:  # noqa: BLE001
            return None

    def decrypt_access(self, info: dict) -> bytes | None:
        """Decrypt an unsegmented access message (app key or known device key)."""
        t = info["transport"]
        if info["ctl"] or not t or t[0] & 0x80:
            return None
        return self._decrypt_upper(info["src"], info["dst"], info["seq"], (t[0] >> 6) & 1, t[0] & 0x3F, 0, t[1:])

    def _segment_ack(self, dst: int, seq_zero: int, block: int) -> bytes:
        params = ((seq_zero & 0x1FFF) << 2).to_bytes(2, "big") + block.to_bytes(4, "big")
        return bytes([PDU_NETWORK]) + self._network_pdu(1, 5, self.next_seq(), dst, b"\x00" + params)

    def handle_pdu(self, data: bytes) -> tuple[list[tuple[int, bytes]], list[bytes]]:
        """Process one complete incoming proxy PDU.

        Returns (access messages as (src, payload), proxy PDUs to send back e.g. segment acks).
        """
        if not data or data[0] & 0x3F != PDU_NETWORK:
            return [], []
        info = self.decrypt_network(data[1:])
        if not info or info["ctl"]:
            return [], []
        t = info["transport"]
        if not t[0] & 0x80:
            acc = self.decrypt_access(info)
            return ([(info["src"], acc)] if acc else []), []
        if len(t) < 5:
            return [], []
        src, seq = info["src"], info["seq"]
        akf, aid = (t[0] >> 6) & 1, t[0] & 0x3F
        szmic = t[1] >> 7
        seq_zero = ((t[1] & 0x7F) << 6) | (t[2] >> 2)
        seg_o = ((t[2] & 0x03) << 3) | (t[3] >> 5)
        seg_n = t[3] & 0x1F
        seq_auth = seq - ((seq - seq_zero) & 0x1FFF)
        key = (src, seq_auth)
        full = (1 << (seg_n + 1)) - 1
        if key in self._rx_done:
            return [], [self._segment_ack(src, seq_zero, full)]
        entry = self._rx_segs.setdefault(key, {})
        entry[seg_o] = t[4:]
        if len(entry) < seg_n + 1:
            return [], []
        del self._rx_segs[key]
        self._rx_done[key] = True
        if len(self._rx_done) > 64:
            self._rx_done.pop(next(iter(self._rx_done)))
        upper = b"".join(entry[i] for i in range(seg_n + 1))
        acc = self._decrypt_upper(src, info["dst"], seq_auth & 0xFFFFFF, akf, aid, szmic, upper)
        ack = [self._segment_ack(src, seq_zero, full)]
        return ([(src, acc)] if acc else []), ack

    def proxy_add_filter(self, addresses: list[int]) -> bytes:
        seq = self.next_seq()
        params = b"\x01" + b"".join(a.to_bytes(2, "big") for a in addresses)
        return bytes([PDU_PROXY_CONFIG]) + self._network_pdu(1, 0, seq, 0x0000, params, nonce_type=0x03)

    def parse_incoming(self, data: bytes) -> tuple[int, bytes] | None:
        """Unsegmented-only convenience used by the test scripts."""
        msgs, _ = self.handle_pdu(data)
        return msgs[0] if msgs else None


class ProxyReassembler:
    """Reassembles proxy-level SAR fragments (notifications from 0x2ADE / 0x2ADC)."""

    def __init__(self) -> None:
        self._buf: bytearray | None = None

    def feed(self, frag: bytes) -> bytes | None:
        if not frag:
            return None
        sar, pdu_type = frag[0] >> 6, frag[0] & 0x3F
        if sar == 0:
            self._buf = None
            return bytes(frag)
        if sar == 1:
            self._buf = bytearray([pdu_type]) + frag[1:]
            return None
        if self._buf is None:
            return None
        self._buf += frag[1:]
        if sar == 3:
            out, self._buf = bytes(self._buf), None
            return out
        return None


def proxy_sar_split(pdu: bytes, max_len: int) -> list[bytes]:
    """Split a proxy PDU into proxy-level SAR fragments if it exceeds the ATT payload size."""
    if len(pdu) <= max_len:
        return [pdu]
    pdu_type = pdu[0] & 0x3F
    body = pdu[1:]
    size = max_len - 1
    parts = [body[i:i + size] for i in range(0, len(body), size)]
    out = []
    for i, p in enumerate(parts):
        sar = 0x40 if i == 0 else (0xC0 if i == len(parts) - 1 else 0x80)
        out.append(bytes([sar | pdu_type]) + p)
    return out


# ---- SIG model messages ---------------------------------------------------
class Tid:
    def __init__(self) -> None:
        self._v = 0

    def __call__(self) -> int:
        self._v = (self._v + 1) & 0xFF
        return self._v


_tid = Tid()


def generic_onoff_get() -> bytes:
    return b"\x82\x01"


def generic_onoff_set(on: bool, ack: bool = True) -> bytes:
    return (b"\x82\x02" if ack else b"\x82\x03") + bytes([1 if on else 0, _tid()])


# ---- Lumineo / Telink vendor commands (company 0x0211, "silan" passthrough) ----
# Reverse-engineered from the Lumineo Dancing Lights app v1.0.52.
GAMMA = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6, 7, 7, 7, 8, 8, 8, 9, 9, 9, 10, 10, 10, 11, 11, 11, 12, 12, 13, 13, 14, 14, 14, 15, 15, 16, 16, 17, 17, 18, 18, 19, 19, 20, 20, 21, 22, 22, 23, 23, 24, 24, 25, 26, 26, 27, 28, 28, 29, 30, 30, 31, 32, 32, 33, 34, 35, 35, 36, 37, 38, 39, 39, 40, 41, 42, 43, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 53, 54, 55, 56, 57, 58, 59, 60, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 73, 74, 75, 76, 77, 78, 80, 81, 82, 83, 85, 86, 87, 88, 90, 91, 92, 94, 95, 96, 98, 99, 100, 102, 103, 105, 106, 108, 109, 111, 112, 114, 115, 117, 118, 120, 121, 123, 124, 126, 127, 129, 131, 132, 134, 136, 137, 139, 141, 142, 144, 146, 148, 149, 151, 153, 155, 156, 158, 160, 162, 164, 166, 167, 169, 171, 173, 175, 177, 179, 181, 183, 185, 187, 189, 191, 193, 195, 197, 199, 201, 203, 205, 207, 210, 212, 214, 216, 218, 220, 223, 225, 227, 229, 232, 234, 236, 239, 241, 243, 246, 248, 250, 253, 255]
WHITE_BALANCE = (1.0, 0.85, 0.4)
BRIGHTNESS_MIN, BRIGHTNESS_MAX = 42, 100
SPEED_MIN, SPEED_MAX, SPEED_DEFAULT = 1, 10, 6

OP_POWER = 0xE0         # power, replies with OP_POWER_STATUS
OP_POWER_GET = 0xE1      # power state query, replies with OP_POWER_STATUS
OP_POWER_STATUS = 0xE3
OP_SCENE = 0xE6
OP_BRIGHTNESS = 0xF3


def _vendor(op: int, params: bytes) -> bytes:
    return bytes([op, 0x11, 0x02]) + params


def vendor_power(on: bool) -> bytes:
    return _vendor(OP_POWER, bytes([1 if on else 0]))


def vendor_power_get() -> bytes:
    return _vendor(OP_POWER_GET, b"")


POWER_STATUS_OP = bytes([OP_POWER_STATUS, 0x11, 0x02])


def vendor_brightness(pct: int) -> bytes:
    pct = max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, int(pct)))
    return _vendor(OP_BRIGHTNESS, bytes([GAMMA[pct]]))


def correct_rgb(rgb: tuple[int, int, int], scene: int | None = None) -> tuple[int, int, int]:
    r, g, b = (int(GAMMA[max(0, min(255, int(c)))] * w) for c, w in zip(rgb, WHITE_BALANCE, strict=False))
    if r == 0 and g == 0 and scene != 45:  # the app doubles pure blue (dim LEDs)
        b = min(b * 2, 122)
    return r, g, b


def vendor_scene(scene: int, speed: int, colors: list[tuple[int, int, int]]) -> bytes:
    par = bytearray([scene, speed & 0xFF, len(colors)])
    for c in colors:
        par += bytes([0, *correct_rgb(c, scene)])
    return _vendor(OP_SCENE, bytes(par))


def parse_status(access: bytes) -> bool | None:
    """Return on/off from a Generic OnOff Status or vendor power status, else None."""
    # Note: Generic OnOff status reflects only the SIG model's flag; the LEDs follow the vendor power.
    if access[:3] == bytes([OP_POWER_STATUS, 0x11, 0x02]) and len(access) >= 4:
        return bool(access[3])
    return None


def hex_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


THEMES: dict[str, list[str]] = {
    "Christmas": ["#FFFFFF", "#FF0000"],
    "Jingle Bells": ["#FF0000", "#FFFFFF", "#00FF00"],
    "Ice": ["#FFFFFF", "#00FFEE", "#00ADFF", "#0078FF"],
    "Lavender": ["#FFC9BD", "#FF92A5", "#BF96E2", "#9D71FF"],
    "Flamingo": ["#FFC9BD", "#FF92A5", "#FF7C6F", "#ECE8FF"],
    "Rainbow": ["#FF0000", "#FFF100", "#00CC00", "#00ADFF", "#9D71FF"],
}

# effect name -> (scene id, kind); "single" uses the light's colour, "multi" the palette
SCENE_STATIC = 0
EFFECTS: dict[str, tuple[int, str]] = {
    "Flash": (1, "single"),
    "Chase": (3, "single"),
    "Random colour": (5, "single"),
    "Fade": (6, "single"),
    "Advanced fade": (7, "multi"),
    "Colour change 1": (8, "multi"),
    "Colour change 2": (9, "multi"),
    "Rainbow waterfall": (10, "single"),
    "Snake waterfall": (11, "multi"),
    "Ant waterfall": (12, "multi"),
    "Collide": (18, "single"),
    "Little fire": (19, "multi"),
    "Wave down": (22, "multi"),
    "Flag": (23, "multi"),
    "Heap up": (24, "single"),
    "Vertical wave": (25, "multi"),
    "Wave up": (27, "multi"),
    "Angry waves": (28, "multi"),
    "Fireworks": (29, "multi"),
    "Glow": (30, "multi"),
    "Snake by snake": (31, "multi"),
    "Runaway": (33, "single"),
    "Snow": (35, "multi"),
    "Multi colours": (36, "multi"),
    "Rainbow fixed": (45, "multi"),
}


# ---- Configuration model (device-key encrypted) ----------------------------
OP_COMPOSITION_STATUS = b"\x02"
OP_APPKEY_STATUS = b"\x80\x03"
OP_MODEL_APP_STATUS = b"\x80\x3e"
OP_NODE_RESET_STATUS = b"\x80\x4a"
CONFIG_MODELS = {0x0000, 0x0001}  # config server/client: never bound to app keys


def opcode_len(access: bytes) -> int:
    if not access:
        return 0
    if access[0] & 0xC0 == 0xC0:
        return 3
    if access[0] & 0x80:
        return 2
    return 1


def config_composition_get(page: int = 0) -> bytes:
    return b"\x80\x08" + bytes([page])


def config_appkey_add(app_key: bytes, net_idx: int = 0, app_idx: int = 0) -> bytes:
    idx = bytes([net_idx & 0xFF, ((net_idx >> 8) & 0x0F) | ((app_idx & 0x0F) << 4), (app_idx >> 4) & 0xFF])
    return b"\x00" + idx + app_key


def config_model_app_bind(element: int, model: int, vendor_cid: int | None = None, app_idx: int = 0) -> bytes:
    mid = struct.pack("<HH", vendor_cid, model) if vendor_cid is not None else struct.pack("<H", model)
    return b"\x80\x3d" + struct.pack("<HH", element, app_idx) + mid


def config_node_reset() -> bytes:
    return b"\x80\x49"


def parse_composition(params: bytes) -> dict:
    """Parse Composition Data page 0 (params after the opcode, starting with the page byte)."""
    d = params[1:]
    cid, pid, vid, crpl, features = struct.unpack_from("<HHHHH", d, 0)
    i, elements = 10, []
    while i + 4 <= len(d):
        _loc, num_s, num_v = struct.unpack_from("<HBB", d, i)
        i += 4
        sig = [struct.unpack_from("<H", d, i + 2 * k)[0] for k in range(num_s)]
        i += 2 * num_s
        vendor = [struct.unpack_from("<HH", d, i + 4 * k) for k in range(num_v)]
        i += 4 * num_v
        elements.append({"sig": sig, "vendor": vendor})
    return {"cid": cid, "pid": pid, "vid": vid, "crpl": crpl, "features": features, "elements": elements}
