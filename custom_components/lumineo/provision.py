"""PB-GATT provisioning (Bluetooth Mesh Profile 5.4) with No OOB authentication.

Runs over the Mesh Provisioning service (0x1827): provisioning PDUs are carried
in proxy PDUs of type 0x03 on Data In (0x2ADB) / Data Out (0x2ADC).
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

try:
    from .mesh import ProxyReassembler, cmac, proxy_sar_split, s1
except ImportError:  # used standalone by the dev scripts
    from mesh import ProxyReassembler, cmac, proxy_sar_split, s1

_LOGGER = logging.getLogger(__name__)

PROV_SERVICE = "00001827-0000-1000-8000-00805f9b34fb"
PROV_IN = "00002adb-0000-1000-8000-00805f9b34fb"
PROV_OUT = "00002adc-0000-1000-8000-00805f9b34fb"
PDU_PROVISIONING = 0x03

INVITE, CAPABILITIES, START, PUBLIC_KEY, INPUT_COMPLETE, CONFIRMATION, RANDOM, DATA, COMPLETE, FAILED = range(10)
FAIL_REASONS = {
    1: "invalid PDU", 2: "invalid format", 3: "unexpected PDU", 4: "confirmation failed",
    5: "out of resources", 6: "decryption failed", 7: "unexpected error", 8: "cannot assign addresses",
}


class ProvisioningError(Exception):
    pass


def k1(n: bytes, salt: bytes, p: bytes) -> bytes:
    return cmac(cmac(salt, n), p)


class Provisioner:
    """Drives one provisioning session over an already-connected bleak client."""

    def __init__(self, client, timeout: float = 20.0) -> None:
        self._client = client
        self._timeout = timeout
        self._rx = ProxyReassembler()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    def _on_notify(self, _char, data: bytearray) -> None:
        pdu = self._rx.feed(bytes(data))
        if pdu and pdu[0] & 0x3F == PDU_PROVISIONING:
            self._queue.put_nowait(pdu[1:])

    async def _send(self, pdu_type: int, params: bytes = b"") -> None:
        proxy_pdu = bytes([PDU_PROVISIONING, pdu_type]) + params
        max_len = max(20, (getattr(self._client, "mtu_size", 23) or 23) - 3)
        for frag in proxy_sar_split(proxy_pdu, max_len):
            await self._client.write_gatt_char(PROV_IN, frag, response=False)

    async def _expect(self, pdu_type: int) -> bytes:
        try:
            pdu = await asyncio.wait_for(self._queue.get(), self._timeout)
        except TimeoutError as err:
            raise ProvisioningError(f"timed out waiting for provisioning PDU {pdu_type}") from err
        if pdu[0] == FAILED:
            reason = pdu[1] if len(pdu) > 1 else 0
            raise ProvisioningError(f"device reported failure: {FAIL_REASONS.get(reason, reason)}")
        if pdu[0] != pdu_type:
            raise ProvisioningError(f"unexpected provisioning PDU {pdu[0]} (wanted {pdu_type})")
        return pdu[1:]

    async def run(self, net_key: bytes, unicast: int, iv_index: int = 0, net_idx: int = 0, flags: int = 0) -> tuple[bytes, int]:
        """Provision the device. Returns (device_key, number_of_elements)."""
        await self._client.start_notify(PROV_OUT, self._on_notify)

        invite = bytes([5])  # attention timer: blink for 5 s so you can see which light it is
        await self._send(INVITE, invite)
        caps = await self._expect(CAPABILITIES)
        if len(caps) < 11:
            raise ProvisioningError("short capabilities PDU")
        num_elements = caps[0]
        algorithms = struct.unpack(">H", caps[1:3])[0]
        if not algorithms & 0x0001:
            raise ProvisioningError("device does not support FIPS P-256 provisioning")
        _LOGGER.debug("capabilities %s, elements=%d", caps.hex(), num_elements)

        start = bytes([0x00, 0x00, 0x00, 0x00, 0x00])  # P-256/CMAC, no OOB public key, No OOB auth
        await self._send(START, start)

        priv = ec.generate_private_key(ec.SECP256R1())
        nums = priv.public_key().public_numbers()
        our_pub = nums.x.to_bytes(32, "big") + nums.y.to_bytes(32, "big")
        await self._send(PUBLIC_KEY, our_pub)
        dev_pub = await self._expect(PUBLIC_KEY)
        if len(dev_pub) != 64:
            raise ProvisioningError("bad device public key")
        try:
            peer = ec.EllipticCurvePublicNumbers(
                int.from_bytes(dev_pub[:32], "big"), int.from_bytes(dev_pub[32:], "big"), ec.SECP256R1()
            ).public_key()
        except ValueError as err:
            raise ProvisioningError("device public key not on curve") from err
        ecdh = priv.exchange(ec.ECDH(), peer)

        conf_inputs = invite + caps[:11] + start + our_pub + dev_pub
        conf_salt = s1(conf_inputs)
        conf_key = k1(ecdh, conf_salt, b"prck")
        auth = bytes(16)
        our_random = os.urandom(16)
        await self._send(CONFIRMATION, cmac(conf_key, our_random + auth))
        dev_conf = await self._expect(CONFIRMATION)
        await self._send(RANDOM, our_random)
        dev_random = await self._expect(RANDOM)
        if cmac(conf_key, dev_random + auth) != dev_conf:
            raise ProvisioningError("device confirmation mismatch")

        prov_salt = s1(conf_salt + our_random + dev_random)
        session_key = k1(ecdh, prov_salt, b"prsk")
        session_nonce = k1(ecdh, prov_salt, b"prsn")[3:]
        dev_key = k1(ecdh, prov_salt, b"prdk")

        data = net_key + struct.pack(">HBIH", net_idx, flags, iv_index, unicast)
        await self._send(DATA, AESCCM(session_key, tag_length=8).encrypt(session_nonce, data, None))
        await self._expect(COMPLETE)
        try:
            await self._client.stop_notify(PROV_OUT)
        except Exception:  # noqa: BLE001 - device may already be rebooting into proxy mode
            pass
        return dev_key, num_elements
