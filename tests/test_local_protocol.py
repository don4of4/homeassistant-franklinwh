"""Frame codec for the aGate's local connection (TCP/9000).

The two wire vectors below were produced by this codec and checked byte for
byte against david2069/franklinwh-local 0.3.0 (the reference implementation,
MIT) across 150 frames, five serials and chunked reads, before being frozen
here. A change that alters them breaks compatibility with real gateways.
"""

import pytest

from custom_components.franklin_wh import local

SN = "FAKEGATE90FJ09J6H4F2"

VECTOR_1301 = bytes.fromhex(
    "7b22636d6454797065223a313330312c2265717569704e6f223a2246414b45474154453930"
    "464a30394a3648344632222c61b4bab2a8667f76736abdb3b8b1a0c2b0bdc1748d858c8a89"
    "8f8c938e918e8a81d3cfd0d2869f9a938ad5cfd98ea7a79b92d4e4d696af98afafaab1b1b3"
    "c0c3a1aca3e6e4f8e6c7f9edeaacc507affdff04b3ccc31112"
)
VECTOR_1101 = bytes.fromhex(
    "7b22636d6454797065223a313130312c2265717569704e6f223a223030303030303030222c"
    "c71a20180ecce5dcd9d023191e170628162327daf3ebf2f0eff5f2f9f4f7f4f0e739353638"
    "ec05fdf9f03b353ff40d070c02f93b4b3dfd16ff1f13171525141a2b08130a4d4b5f4d2e60"
    "5451132c6e1664666b1a332a271e6a676d50737177736875735e6e7c2d462f64403e414241"
    "4445389495"
)


def test_seed_is_derived_from_the_serial():
    assert local.derive_seed("00000000") == 0xA5, "the login handshake seed"
    assert local.derive_seed(SN) == 0x3F
    assert local.derive_seed("10060006A02F24360019") != 0x3F, (
        "a hardcoded seed mis-encodes every other gateway's frames"
    )


def test_encode_matches_the_reference_wire_format():
    assert (
        local.encode_frame(1301, SN, {"opt": 0}, time_stamp=1742739351, snno=4)
        == VECTOR_1301
    )
    assert (
        local.encode_frame(
            1101, "00000000", {"opt": 0, "minProtocolVer": "V1.00.00"},
            time_stamp=1742739351, snno=1,
        )
        == VECTOR_1101
    )


def test_header_is_cleartext_and_body_is_not():
    wire = local.encode_frame(1727, SN, {"opt": 3, "current_id": 70189})
    assert wire.startswith(b'{"cmdType":1727,"equipNo":"' + SN.encode() + b'",')
    assert b"current_id" not in wire


def test_decode_recovers_the_frame_without_knowing_the_serial():
    frames = local.FrameBuffer().feed(VECTOR_1301)
    assert len(frames) == 1
    assert (frames[0].cmd_type, frames[0].equip_no, frames[0].data_area) == (
        1301, SN, {"opt": 0},
    )


@pytest.mark.parametrize("cut", [1, 20, 44, 45, 60, 100, len(VECTOR_1301) - 1])
def test_decode_survives_any_tcp_segmentation(cut):
    buf = local.FrameBuffer()
    assert buf.feed(VECTOR_1301[:cut]) == []
    frames = buf.feed(VECTOR_1301[cut:])
    assert [f.cmd_type for f in frames] == [1301]


def test_several_frames_in_one_read_and_leading_garbage():
    a = local.encode_frame(1302, SN, {"soc": 99.4, "name": "TOU"})
    b = local.encode_frame(1726, SN, {"current_id": 5, "list": []})
    frames = local.FrameBuffer().feed(b"\x00junk" + a + b)
    assert [f.cmd_type for f in frames] == [1302, 1726]
    assert frames[0].data_area["name"] == "TOU"


def test_nested_braces_and_quotes_in_strings_do_not_end_the_frame_early():
    data = {"list": [{"id": 1, "name": 'A "quoted" {brace} \\ name'}], "current_id": 1}
    frames = local.FrameBuffer().feed(local.encode_frame(1726, SN, data))
    assert frames[0].data_area == data


def test_runaway_input_is_bounded():
    buf = local.FrameBuffer()
    with pytest.raises(local.LocalGatewayError):
        buf.feed(b'{"cmdType":1,"equipNo":"X",' + b"\x00" * (300 * 1024))


def test_mode_list_helpers():
    modes = {
        "current_id": 70189,
        "list": [
            {"id": 135494, "name": "Emergency Backup", "reserved_soc": 100, "scheduling_type": 3},
            {"id": 70189, "name": "TOU", "reserved_soc": 35, "scheduling_type": 1},
        ],
    }
    assert local.active_mode(modes) == ("time_of_use", 35)
    assert local.available_modes(modes) == ["emergency_backup", "time_of_use"], (
        "a gateway with no Self Consumption programme must not offer it"
    )
    assert local.active_mode({"current_id": 9, "list": []}) == (None, None)
    # ids arrive as ints or strings depending on firmware
    modes["current_id"] = "70189"
    assert local.active_mode(modes)[0] == "time_of_use"
