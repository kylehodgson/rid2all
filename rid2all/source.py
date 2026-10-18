"""MAVLink serial source: reads the DroneScout radio and dispatches snapshots.

Handles both output formats the DroneScout bridge supports
(OPEN_DRONE_ID_MESSAGE_PACK and ADSB_VEHICLE) simultaneously — whichever the
radio is configured for gets parsed; no daemon restart needed when the radio
mode changes.
"""

import logging
import threading
import time

from pymavlink import mavutil

from . import odid

log = logging.getLogger("rid2all.source")


class MavlinkSource:
    def __init__(self, device: str, baud: int, on_odid, on_adsb,
                 reconnect_delay: float = 5.0):
        self.device = device
        self.baud = baud
        self.on_odid = on_odid
        self.on_adsb = on_adsb
        self.reconnect_delay = reconnect_delay
        self._stopping = threading.Event()
        self.packs_seen = 0
        self.adsb_seen = 0
        self.parse_errors = 0
        self._mode_logged = set()

    def stop(self):
        self._stopping.set()

    def run(self):
        """Blocking connect/parse loop with reconnect. Returns on stop."""
        while not self._stopping.is_set():
            try:
                conn = mavutil.mavlink_connection(self.device, self.baud)
                conn.wait_heartbeat(timeout=30)
                log.info("opened %s @ %d, heartbeat ok", self.device, self.baud)
                self._read_loop(conn)
            except Exception as exc:  # noqa: BLE001 - keep the daemon alive
                if self._stopping.is_set():
                    break
                log.exception("source error (%s: %s), retry in %.0fs",
                              type(exc).__name__, exc, self.reconnect_delay)
                self._stopping.wait(self.reconnect_delay)
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    def _read_loop(self, conn):
        last_stats = time.time()
        consecutive_errors = 0
        while not self._stopping.is_set():
            try:
                msg = conn.recv_match()
            except Exception as exc:  # noqa: BLE001 - wire noise is expected
                consecutive_errors += 1
                if consecutive_errors % 50 == 1:
                    log.warning("frame decode error: %s", exc)
                if consecutive_errors >= 50:
                    time.sleep(0.1)
                continue
            consecutive_errors = 0
            if msg is None:
                time.sleep(0.02)
                now = time.time()
                if now - last_stats >= 30:
                    last_stats = now
                    log.info("stats: %d odid packs, %d adsb, %d parse errors",
                             self.packs_seen, self.adsb_seen, self.parse_errors)
                continue

            msg_type = msg.get_type()
            if msg_type == "OPEN_DRONE_ID_MESSAGE_PACK":
                self._mode_logged.add("odid")
                self.packs_seen += 1
                try:
                    payload = bytes(bytearray(msg.messages))
                    snapshot = odid.parse_message_pack(payload, msg.msg_pack_size)
                except (IndexError, struct_error()) as exc:
                    self.parse_errors += 1
                    log.warning("bad ODID pack: %s", exc)
                    continue
                if snapshot is None:
                    continue
                self.on_odid(snapshot)

            elif msg_type == "ADSB_VEHICLE":
                self._mode_logged.add("adsb")
                self.adsb_seen += 1
                try:
                    snapshot = odid.parse_adsb_vehicle(msg)
                except (IndexError, AttributeError) as exc:
                    self.parse_errors += 1
                    log.warning("bad ADSB_VEHICLE: %s", exc)
                    continue
                self.on_adsb(snapshot)

            elif msg_type == "HEARTBEAT" and "heartbeat" not in self._mode_logged:
                self._mode_logged.add("heartbeat")

    def detected_mode(self) -> str:
        if "odid" in self._mode_logged and "adsb" in self._mode_logged:
            return "odid+adsb"
        if "odid" in self._mode_logged:
            return "opendroneid"
        if "adsb" in self._mode_logged:
            return "adsb_vehicle"
        return "unknown"


def struct_error():
    import struct
    return struct.error
