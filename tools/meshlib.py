"""Standalone helpers for the test scripts; the protocol lives in custom_components/lumineo/mesh.py."""
import json
import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "custom_components", "lumineo"))

from mesh import (  # noqa: E402,F401
    EFFECTS,
    PROXY_IN,
    PROXY_OUT,
    THEMES,
    MeshCrypto,
    generic_onoff_get,
    generic_onoff_set,
    hex_rgb,
    key_from_string,
    parse_status,
    vendor_brightness,
    vendor_power,
    vendor_power_get,
    vendor_scene,
)


class Mesh(MeshCrypto):
    """MeshCrypto configured from the app's share JSON, with seq persisted to seq.json."""

    def __init__(self, cfg_path: str, src: int = 0x00F0):
        cfg = json.load(open(cfg_path))
        self.nodes = cfg["nodes"]
        self.state_path = os.path.join(os.path.dirname(os.path.abspath(cfg_path)), "seq.json")
        try:
            self.seq = json.load(open(self.state_path))["seq"]
        except (FileNotFoundError, KeyError, ValueError):
            self.seq = 1
        super().__init__(key_from_string(cfg["meshPwd"]), key_from_string(cfg["meshName"]), src, self._next_seq, cfg.get("ivIndex", 0))

    def _next_seq(self) -> int:
        s = self.seq
        self.seq += 1
        with open(self.state_path, "w") as f:
            json.dump({"seq": self.seq}, f)
        return s

    def access_pdu(self, dst: int, access: bytes, ttl: int = 5) -> bytes:
        pdus = self.access_pdus(dst, access, ttl)
        if len(pdus) != 1:
            raise ValueError("message needs segmentation; use access_pdus()")
        return pdus[0]

    def decrypt_proxy_config(self, pdu: bytes):
        return self.decrypt_network(pdu, nonce_type=0x03)
