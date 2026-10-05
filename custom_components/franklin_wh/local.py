"""Local connection to the aGate (TCP/9000): mode read and mode switch.

The aGate serves the Franklin app's "Direct Connection" protocol on TCP port
9000 of its LAN address. It carries the same JSON cmdType frames the cloud
relays through sendMqtt, so the operating mode can be read and changed with no
cloud login at all - which matters because the cloud login is the part that
breaks (it returned 502s for days from 2026-10-03).

Protocol reverse-engineered by david2069 (github.com/david2069/franklinwh-local,
MIT) with the per-serial seed formula recovered by voidstarr
(github.com/voidstarr/franklinwh_local, MIT). The approach of selecting the
gateway's own mode programme and confirming the change against a local read
follows mtnears/FranklinWH-Automation v4.7 (MIT). This is an independent async
implementation of the parts this integration needs; it has no dependencies and
deliberately does not import Home Assistant.

Wire format: one JSON object per frame. The header is cleartext and everything
from "type" onward is shifted by a keyless position-dependent byte offset:

    {"cmdType":<N>,"equipNo":"<SN>",        <- cleartext
    "type":0,"timeStamp":..,"snno":..,"len":L,"crc":"C","dataArea":{..}}
                                             <- c[i] = (p[i] + seed + i) & 0xFF

    seed = (sum(utf8(SN)) + len(SN) + 29) & 0xFF
    len  = byte length of the compact dataArea JSON
    crc  = CRC32 of that same string, 8 uppercase hex digits

There is NO authentication: the 1101 handshake is sent to equipNo "00000000"
and the 1102 reply names the gateway's serial, which addresses every later
request. Anything on the LAN that can reach port 9000 can do what this does.

Only two things are written here: nothing by the reads, and 1727 opt=3 (select
the active mode programme) by set_mode. That command selects one of the
gateway's existing programmes, each of which keeps its own reserve SOC, so a
local mode change cannot overwrite a reserve. Reserves and export settings have
no local write path and stay on the cloud.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import re
import time
from typing import Any
import zlib

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 9000
DEFAULT_TIMEOUT = 8.0

CMD_LOGIN = 1101  # -> 1102 manifest: IBG_SN (serial) and firmware versions
CMD_POWER_FLOW = 1301  # -> 1302 soc, p_uti, p_sun, p_fhp, p_load, mode, kwh_*
CMD_MODE_LIST = 1725  # -> 1726 current_id + list[{id, name, reserved_soc, scheduling_type}]
CMD_MODE_SET = 1727  # -> 1728 opt=3 + current_id selects the active programme
CMD_ERROR = 9999  # generic "unsupported / rejected" reply

LOGIN_EQUIP = "00000000"
_LOGIN_DATA = {"opt": 0, "minProtocolVer": "V1.00.00"}

# scheduling_type on a mode-list entry is the cloud's workMode: stable across
# firmware and accounts, unlike the programme ids, which are per-gateway.
MODE_BY_SCHEDULING_TYPE = {
    1: "time_of_use",
    2: "self_consumption",
    3: "emergency_backup",
}
SCHEDULING_TYPE_BY_MODE = {v: k for k, v in MODE_BY_SCHEDULING_TYPE.items()}

_HEADER_RE = re.compile(rb'\{"cmdType":(\d+),"equipNo":"([0-9A-Za-z]*)",')
_FIRST_PLAINTEXT_BYTE = 0x22  # the ciphered region always starts with '"'
_MAX_FRAME_BYTES = 256 * 1024


class LocalGatewayError(Exception):
    """The local connection failed; `stage` says where.

    stage is one of: connect, login, request, refused, unconfirmed,
    unsupported (the gateway has no programme for the requested mode).
    """

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"[{stage}] {message}")
        self.stage = stage


# -- frame codec -------------------------------------------------------------


def derive_seed(equip_no: str) -> int:
    """Obfuscation seed for frames addressed to `equip_no`."""
    raw = (equip_no or LOGIN_EQUIP).encode("utf-8")
    return (sum(raw) + len(raw) + 29) & 0xFF


def _shift(data: bytes, seed: int, sign: int) -> bytes:
    return bytes((b + sign * (seed + i)) & 0xFF for i, b in enumerate(data))


def encode_frame(
    cmd_type: int, equip_no: str, data_area: Any, *, time_stamp: int = 0, snno: int = 0
) -> bytes:
    """Build one on-the-wire frame."""
    body = json.dumps(data_area, separators=(",", ":"))
    crc = f"{zlib.crc32(body.encode('latin1')) & 0xFFFFFFFF:08X}"
    header = f'{{"cmdType":{cmd_type},"equipNo":"{equip_no}",'
    rest = (
        f'"type":0,"timeStamp":{time_stamp},"snno":{snno},'
        f'"len":{len(body.encode("latin1"))},"crc":"{crc}","dataArea":{body}}}'
    )
    return header.encode("latin1") + _shift(
        rest.encode("latin1"), derive_seed(equip_no), +1
    )


@dataclass
class Frame:
    """One decoded frame."""

    cmd_type: int
    equip_no: str
    data_area: Any


def _balanced_json_end(text: str) -> int | None:
    """Index just past the first balanced top-level {...}, or None."""
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


class FrameBuffer:
    """Accumulates bytes from the socket and yields complete frames.

    The seed is recovered from the first ciphered byte of each frame (the
    plaintext there is always a double quote), so replies decode without
    knowing the serial in advance - which is how the login reply is read.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buf.extend(data)
        if len(self._buf) > _MAX_FRAME_BYTES:
            self._buf.clear()
            raise LocalGatewayError("request", "reply exceeded the frame size limit")
        frames: list[Frame] = []
        while True:
            match = _HEADER_RE.match(self._buf)
            if not match:
                # Resynchronise on the next header, if there is one.
                nxt = self._buf.find(b'{"cmdType":', 1)
                if nxt > 0:
                    del self._buf[:nxt]
                    continue
                break
            header_end = match.end()
            region = bytes(self._buf[header_end:])
            if not region:
                break
            seed = (region[0] - _FIRST_PLAINTEXT_BYTE) & 0xFF
            text = self._buf[:header_end].decode("latin1") + _shift(
                region, seed, -1
            ).decode("latin1")
            end = _balanced_json_end(text)
            if end is None:
                break  # need more bytes
            try:
                obj = json.loads(text[:end])
            except json.JSONDecodeError:
                del self._buf[:header_end]
                continue
            frames.append(
                Frame(int(match.group(1)), match.group(2).decode(), obj.get("dataArea", {}))
            )
            del self._buf[:end]
        return frames


# -- session -----------------------------------------------------------------


class _Session:
    """One connect + login; requests are addressed to the serial it learned."""

    def __init__(self, reader, writer, timeout: float) -> None:
        self._reader = reader
        self._writer = writer
        self._timeout = timeout
        self._frames = FrameBuffer()
        self._pending: list[Frame] = []
        self._snno = 0
        self.equip_no = LOGIN_EQUIP
        self.manifest: dict[str, Any] = {}

    async def _recv(self, want: int) -> Frame:
        deadline = time.monotonic() + self._timeout
        while True:
            for i, frame in enumerate(self._pending):
                if frame.cmd_type in (want, CMD_ERROR):
                    return self._pending.pop(i)
            # Anything else the gateway volunteers is not our reply; drop it.
            self._pending.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no {want} reply within {self._timeout}s")
            chunk = await asyncio.wait_for(self._reader.read(8192), remaining)
            if not chunk:
                raise ConnectionError("connection closed by the aGate")
            self._pending.extend(self._frames.feed(chunk))

    async def request(self, cmd_type: int, data_area: Any) -> dict[str, Any]:
        self._snno += 1
        self._writer.write(
            encode_frame(
                cmd_type,
                self.equip_no,
                data_area,
                time_stamp=int(time.time()),
                snno=self._snno,
            )
        )
        await asyncio.wait_for(self._writer.drain(), self._timeout)
        frame = await self._recv(cmd_type + 1)
        if frame.cmd_type == CMD_ERROR:
            raise LocalGatewayError(
                "request", f"aGate rejected cmdType {cmd_type}: {frame.data_area}"
            )
        return frame.data_area if isinstance(frame.data_area, dict) else {}

    async def login(self) -> None:
        self.manifest = await self.request(CMD_LOGIN, _LOGIN_DATA)
        serial = self.manifest.get("IBG_SN")
        if not serial:
            raise LocalGatewayError("login", "handshake reply carried no serial")
        self.equip_no = serial


class LocalGateway:
    """Short-session client: every call connects, logs in, asks, and closes.

    Nothing is held open between calls, so a firmware update, a reboot or a
    Wi-Fi blip costs one failed call, not a stuck socket.
    """

    def __init__(
        self, host: str, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.serial: str | None = None
        self.firmware: str | None = None

    async def _run(self, fn):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        except (OSError, TimeoutError) as err:
            raise LocalGatewayError(
                "connect", f"{self.host}:{self.port}: {type(err).__name__} {err}".strip()
            ) from err
        try:
            session = _Session(reader, writer, self.timeout)
            try:
                await session.login()
            except LocalGatewayError as err:
                raise LocalGatewayError("login", str(err)) from err
            except (OSError, TimeoutError, ConnectionError) as err:
                raise LocalGatewayError(
                    "login", f"{type(err).__name__} {err}".strip()
                ) from err
            self.serial = session.equip_no
            self.firmware = session.manifest.get("APP_VER")
            try:
                return await fn(session)
            except LocalGatewayError:
                raise
            except (OSError, TimeoutError, ConnectionError) as err:
                raise LocalGatewayError(
                    "request", f"{type(err).__name__} {err}".strip()
                ) from err
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 2)
            except (OSError, TimeoutError):
                pass

    # -- reads ---------------------------------------------------------------

    async def mode_list(self) -> dict[str, Any]:
        """The gateway's mode programmes and which one is active."""
        return await self._run(lambda s: s.request(CMD_MODE_LIST, {"opt": 1}))

    async def power_flow(self) -> dict[str, Any]:
        """Live power flow, SOC and today's energy totals."""
        return await self._run(lambda s: s.request(CMD_POWER_FLOW, {"opt": 0}))

    async def read_mode(self) -> tuple[str | None, int | None]:
        """(operating mode, that mode's reserve SOC), or (None, None) if unknown."""
        return active_mode(await self.mode_list())

    # -- write ---------------------------------------------------------------

    async def set_mode(
        self, mode: str, confirm_timeout: float = 15.0, confirm_delay: float = 1.0
    ) -> None:
        """Select the gateway's own programme for `mode` and confirm it took.

        The aGate acknowledges with result 0 as soon as the frame parses, which
        is not proof the setting applied, so the change is only reported as
        done once a fresh mode-list read shows the new programme as active.
        Raises LocalGatewayError otherwise - the caller decides what to fall
        back to.
        """
        scheduling_type = SCHEDULING_TYPE_BY_MODE.get(mode)
        if scheduling_type is None:
            raise LocalGatewayError("request", f"unknown operating mode {mode!r}")

        async def send(session: _Session) -> int:
            modes = await session.request(CMD_MODE_LIST, {"opt": 1})
            programme = _programme_id(modes, scheduling_type)
            if programme is None:
                raise LocalGatewayError(
                    "unsupported", f"this aGate has no {mode} programme"
                )
            reply = await session.request(
                CMD_MODE_SET, {"opt": 3, "current_id": programme}
            )
            if reply.get("result") not in (0, None):
                raise LocalGatewayError(
                    "refused", f"aGate refused the mode change: {reply}"
                )
            return programme

        programme = await self._run(send)

        deadline = time.monotonic() + confirm_timeout
        delay = confirm_delay
        seen: Any = None
        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            try:
                seen = (await self.mode_list()).get("current_id")
            except LocalGatewayError as err:
                _LOGGER.debug("Mode confirmation read failed: %s", err)
            else:
                if _same_id(seen, programme):
                    return
            delay = min(delay * 1.5, 4.0)
        raise LocalGatewayError(
            "unconfirmed",
            f"sent {mode} (programme {programme}) but the aGate still reports "
            f"programme {seen}",
        )


def _same_id(a: Any, b: Any) -> bool:
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def _programme_id(modes: dict[str, Any], scheduling_type: int) -> int | None:
    for entry in modes.get("list") or []:
        if entry.get("scheduling_type") == scheduling_type:
            try:
                return int(entry["id"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def available_modes(modes: dict[str, Any]) -> list[str]:
    """Operating modes this gateway has a programme for, in list order.

    Not every gateway has all three: a site set up without Self Consumption
    simply has no such programme, and there is nothing to switch to.
    """
    found = []
    for entry in modes.get("list") or []:
        mode = MODE_BY_SCHEDULING_TYPE.get(entry.get("scheduling_type"))
        if mode and mode not in found:
            found.append(mode)
    return found


def active_mode(modes: dict[str, Any]) -> tuple[str | None, int | None]:
    """(active operating mode, its reserve SOC) from a mode-list reply."""
    current = modes.get("current_id")
    for entry in modes.get("list") or []:
        if _same_id(entry.get("id"), current):
            mode = MODE_BY_SCHEDULING_TYPE.get(entry.get("scheduling_type"))
            reserve = entry.get("reserved_soc")
            try:
                reserve = int(reserve) if reserve is not None else None
            except (TypeError, ValueError):
                reserve = None
            return mode, reserve
    return None, None
