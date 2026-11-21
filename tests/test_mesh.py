"""Protocol tests: Bluetooth Mesh spec sample data (Mesh Profile 1.0.1, section 8.1) and round trips."""
import mesh
import pytest
from provision import k1

H = bytes.fromhex


def test_s1():
    assert mesh.s1(b"test") == H("b73cefbd641ef2ea598c2b6efb62f79c")


def test_k1():
    out = k1(H("3216d1509884b533248541792b877f98"), H("2ba14ffa0df84a2831938d57d276cab4"),
             H("5a09d60797eeb4478aada59db3352a0d"))
    assert out == H("f6ed15a8934afbe7d83e8dcb57fcf5d7")


def test_k2_master():
    nid, enc, priv = mesh.k2(H("f7a2a44f8e8a8029064f173ddc1e2b00"), b"\x00")
    assert nid == 0x7F
    assert enc == H("9f589181a0f50de73c8070c7a6d27f46")
    assert priv == H("4c715bd4a64b938f99b453351653124f")


def test_k3():
    assert mesh.k3(H("f7a2a44f8e8a8029064f173ddc1e2b00")) == H("ff046958233db014")


def test_k4():
    assert mesh.k4(H("3216d1509884b533248541792b877f98")) == 0x38


def test_key_from_string():
    assert mesh.key_from_string("LIGHTSTRING12345") == b"LIGHTSTRING12345"
    with pytest.raises(ValueError):
        mesh.key_from_string("short")


def _pair(net=b"0123456789ABCDEF", app=b"LIGHTSTRING12345"):
    counters = {"a": 0, "b": 0}

    def seq(name):
        def nxt():
            counters[name] += 1
            return counters[name]
        return nxt

    tx = mesh.MeshCrypto(net, app, 0x00F1, seq("a"))
    rx = mesh.MeshCrypto(net, app, 0x0001, seq("b"))
    return tx, rx


def test_unsegmented_round_trip():
    tx, rx = _pair()
    msg = mesh.vendor_power(True)
    (pdu,) = tx.access_pdus(0x0001, msg)
    msgs, acks = rx.handle_pdu(pdu)
    assert msgs == [(0x00F1, msg)]
    assert acks == []


def test_segmented_round_trip_sends_ack():
    tx, rx = _pair()
    msg = mesh.vendor_scene(5, 3, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 255, 255)])
    pdus = tx.access_pdus(0x0001, msg)
    assert len(pdus) > 1
    got = []
    for pdu in pdus:
        msgs, acks = rx.handle_pdu(pdu)
        got += msgs
    assert got == [(0x00F1, msg)]
    assert len(acks) == 1


def test_device_key_round_trip():
    tx, rx = _pair()
    dev_key = H("00112233445566778899aabbccddeeff")
    rx.device_keys[0x00F1] = dev_key
    msg = mesh.config_node_reset()
    (pdu,) = tx.access_pdus(0x0001, msg, dev_key=dev_key)
    assert rx.handle_pdu(pdu)[0] == [(0x00F1, msg)]


def test_wrong_network_key_is_ignored():
    tx, _ = _pair()
    _, other = _pair(net=b"FEDCBA9876543210")
    (pdu,) = tx.access_pdus(0x0001, mesh.vendor_power(False))
    assert other.handle_pdu(pdu) == ([], [])


@pytest.mark.parametrize("size", [5, 19, 20, 21, 60, 200])
def test_proxy_sar(size):
    pdu = bytes([mesh.PDU_NETWORK]) + bytes(range(size % 256)) * (size // 256 + 1)
    pdu = pdu[: size + 1]
    rx = mesh.ProxyReassembler()
    out = None
    for frag in mesh.proxy_sar_split(pdu, 20):
        assert len(frag) <= 20
        out = rx.feed(frag)
    assert out == pdu


def test_vendor_messages():
    assert mesh.vendor_power(True) == H("e0110201")
    assert mesh.vendor_power(False) == H("e0110200")
    assert mesh.vendor_power_get() == H("e11102")
    assert mesh.vendor_brightness(255) == H("f31102ff")  # full brightness
    assert mesh.vendor_brightness(42) == H("f31102") + bytes([mesh.GAMMA[42]])  # app minimum
    assert mesh.vendor_brightness(0) == mesh.vendor_brightness(42)  # clamped
    assert mesh.vendor_brightness(500) == mesh.vendor_brightness(255)  # clamped
    scene = mesh.vendor_scene(mesh.SCENE_STATIC, 5, [(255, 255, 255)])
    assert scene[:6] == H("e61102000501")
    assert scene[6:] == bytes([0, 255, int(255 * 0.85), int(255 * 0.4)])


def test_pure_blue_is_boosted():
    assert mesh.correct_rgb((0, 0, 255)) == (0, 0, 122)


def test_parse_status():
    assert mesh.parse_status(H("e3110201")) is True
    assert mesh.parse_status(H("e3110200")) is False
    assert mesh.parse_status(H("8204 01")) is None  # Generic OnOff status doesn't reflect the LEDs


def test_opcode_len():
    assert mesh.opcode_len(H("02")) == 1
    assert mesh.opcode_len(H("8003")) == 2
    assert mesh.opcode_len(H("e01102")) == 3
