"""LocalGateway against an in-process fake aGate speaking the real framing.

What matters here is the write path: a mode change is only reported as done
when a fresh read shows the new programme active. The aGate answers result 0
as soon as a frame parses, which is not proof that anything changed.
"""

import asyncio

import pytest
from conftest import run

from custom_components.franklin_wh import local

SN = "10060006A02F24360019"
TOU, BACKUP, SELF = 70189, 135494, 4242


class FakeGate:
    """Minimal aGate: login, mode list, mode set, power flow."""

    def __init__(self, *, programmes=None, current=TOU, apply=True, result=0,
                 chatter=False, mute_set=False, reject_login=False):
        self.programmes = programmes or [
            {"id": BACKUP, "name": "Emergency Backup", "reserved_soc": 100, "scheduling_type": 3},
            {"id": TOU, "name": "TOU", "reserved_soc": 35, "scheduling_type": 1},
            {"id": SELF, "name": "Self", "reserved_soc": 20, "scheduling_type": 2},
        ]
        self.current, self.apply, self.result = current, apply, result
        self.chatter, self.mute_set, self.reject_login = chatter, mute_set, reject_login
        self.requests = []  # (cmd, dataArea) in arrival order
        self.sessions = 0

    def _reply(self, frame):
        self.requests.append((frame.cmd_type, frame.data_area))
        if frame.cmd_type == 1101:
            if self.reject_login:
                return [(1102, {})]
            assert frame.equip_no == "00000000", "login is addressed to the null serial"
            return [(1102, {"IBG_SN": SN, "APP_VER": "V10R81B42D00"})]
        assert frame.equip_no == SN, "every later request is addressed to the serial"
        if frame.cmd_type == 1725:
            return [(1726, {"current_id": self.current, "list": self.programmes})]
        if frame.cmd_type == 1727:
            if self.mute_set:
                return []
            if frame.data_area.get("opt") == 3 and self.result == 0 and self.apply:
                self.current = frame.data_area["current_id"]
            return [(1728, {"opt": 3, "result": self.result})]
        if frame.cmd_type == 1301:
            return [(1302, {"soc": 99.4, "mode": self.current, "name": "TOU"})]
        return [(9999, {"result": 1, "reason": 4})]

    async def _serve(self, reader, writer):
        self.sessions += 1
        buf = local.FrameBuffer()
        try:
            while chunk := await reader.read(8192):
                for frame in buf.feed(chunk):
                    if self.chatter:  # unsolicited frame ahead of the real reply
                        writer.write(local.encode_frame(1302, SN, {"soc": 1}))
                    for cmd, data in self._reply(frame):
                        writer.write(local.encode_frame(cmd, SN, data))
                    await writer.drain()
        finally:
            writer.close()


def with_gate(gate, scenario, **kw):
    async def main():
        server = await asyncio.start_server(gate._serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await scenario(local.LocalGateway("127.0.0.1", port, **kw))
        finally:
            server.close()
            await server.wait_closed()
    return run(main())


def test_read_mode_reports_the_active_programme_and_its_reserve():
    gate = FakeGate()
    assert with_gate(gate, lambda gw: gw.read_mode()) == ("time_of_use", 35)
    assert [c for c, _ in gate.requests] == [1101, 1725]
    assert gate.requests[1][1] == {"opt": 1}


def test_serial_and_firmware_are_learned_from_the_handshake():
    async def scenario(gw):
        await gw.power_flow()
        return gw.serial, gw.firmware
    assert with_gate(FakeGate(), scenario) == (SN, "V10R81B42D00")


def test_set_mode_selects_the_gateways_own_programme_and_confirms():
    gate = FakeGate()
    with_gate(gate, lambda gw: gw.set_mode("emergency_backup", confirm_delay=0.01))
    sets = [d for c, d in gate.requests if c == 1727]
    assert sets == [{"opt": 3, "current_id": BACKUP}], (
        "exactly one write, carrying only the programme id - no reserve field"
    )
    assert gate.current == BACKUP
    assert gate.sessions >= 2, "confirmation must be a fresh session, not the send's echo"


def test_an_acknowledged_but_unapplied_change_is_not_reported_as_success():
    """result 0 means 'frame parsed'. The read-back is the only proof."""
    gate = FakeGate(apply=False)
    with pytest.raises(local.LocalGatewayError) as err:
        with_gate(gate, lambda gw: gw.set_mode(
            "emergency_backup", confirm_timeout=0.2, confirm_delay=0.01))
    assert err.value.stage == "unconfirmed"
    assert gate.current == TOU


def test_a_refused_change_raises_and_is_not_retried():
    gate = FakeGate(result=1)
    with pytest.raises(local.LocalGatewayError) as err:
        with_gate(gate, lambda gw: gw.set_mode("emergency_backup", confirm_delay=0.01))
    assert err.value.stage == "refused"
    assert sum(1 for c, _ in gate.requests if c == 1727) == 1


def test_a_mode_with_no_programme_is_unsupported_and_writes_nothing():
    gate = FakeGate(programmes=[
        {"id": BACKUP, "name": "Emergency Backup", "reserved_soc": 100, "scheduling_type": 3},
        {"id": TOU, "name": "TOU", "reserved_soc": 35, "scheduling_type": 1},
    ])
    with pytest.raises(local.LocalGatewayError) as err:
        with_gate(gate, lambda gw: gw.set_mode("self_consumption"))
    assert err.value.stage == "unsupported"
    assert not [c for c, _ in gate.requests if c == 1727], "nothing may be written"


def test_unknown_mode_name_never_reaches_the_gateway():
    gate = FakeGate()
    with pytest.raises(local.LocalGatewayError):
        with_gate(gate, lambda gw: gw.set_mode("party_mode"))
    assert gate.requests == []


def test_unsolicited_frames_are_not_mistaken_for_the_reply():
    gate = FakeGate(chatter=True)
    assert with_gate(gate, lambda gw: gw.read_mode()) == ("time_of_use", 35)


def test_a_silent_gateway_times_out_in_the_request_stage():
    gate = FakeGate(mute_set=True)
    with pytest.raises(local.LocalGatewayError) as err:
        with_gate(gate, lambda gw: gw.set_mode("emergency_backup"), timeout=0.2)
    assert err.value.stage == "request"


def test_closed_port_is_a_connect_stage_failure():
    async def scenario():
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        await local.LocalGateway("127.0.0.1", port, timeout=1).read_mode()
    with pytest.raises(local.LocalGatewayError) as err:
        run(scenario())
    assert err.value.stage == "connect"


def test_handshake_without_a_serial_is_a_login_stage_failure():
    with pytest.raises(local.LocalGatewayError) as err:
        with_gate(FakeGate(reject_login=True), lambda gw: gw.read_mode())
    assert err.value.stage == "login"


def test_unsupported_command_reply_is_an_error_not_a_hang():
    async def scenario(gw):
        return await gw._run(lambda s: s.request(1999, {"opt": 0}))
    with pytest.raises(local.LocalGatewayError) as err:
        with_gate(FakeGate(), scenario)
    assert err.value.stage == "request"
