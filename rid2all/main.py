"""rid2all — Remote ID to anything.

Reads Remote ID from DroneScout radios (MAVLink OpenDroneID or ADSB_VEHICLE
over serial) and emits SBS BaseStation to tracking networks: serve it to a
local readsb/ultrafeeder, or push it straight to aggregators like
wellclear.aero.
"""

import argparse
import logging
import signal
import sys
import threading
import time

from . import __version__
from .config import load_config
from .sbs import SBSManager, encode_message
from .source import MavlinkSource
from .track import TrackTable

log = logging.getLogger("rid2all")


def _setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


class Daemon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tracks = TrackTable(cfg["track"]["expire_seconds"])
        self.sbs = SBSManager(cfg["sbs"]["listen"], cfg["sbs"]["push"])
        self.source = MavlinkSource(
            cfg["source"]["device"], cfg["source"]["baud"],
            on_odid=self._on_odid, on_adsb=self._on_adsb,
        )
        self._stopping = threading.Event()
        self.last_pos_emit: dict[str, float] = {}
        self.last_ident_emit: dict[str, float] = {}

    def _on_odid(self, snapshot: dict):
        self.tracks.update_odid(snapshot)

    def _on_adsb(self, snapshot: dict):
        self.tracks.update_adsb_vehicle(snapshot)

    def _emit(self):
        """Emit SBS lines for all live tracks at the configured cadence."""
        cfg = self.cfg["track"]
        now = time.time()
        self.tracks.expire(now)
        for icao, track in self.tracks.tracks.items():
            if track.lat is None or track.lon is None:
                continue
            lines = []
            last_ident = self.last_ident_emit.get(icao, 0.0)
            if track.new or now - last_ident >= cfg["ident_interval"]:
                lines.append(encode_message(
                    1, icao, callsign=track.callsign, now=now))
                self.last_ident_emit[icao] = now
                track.new = False
            last_pos = self.last_pos_emit.get(icao, 0.0)
            if now - last_pos >= cfg["position_interval"]:
                lines.append(encode_message(
                    3, icao, alt_ft=track.alt_ft, lat=track.lat, lon=track.lon,
                    vr_fpm=track.vr_fpm, squawk=track.squawk,
                    alert=1 if track.emergency else 0,
                    emergency=1 if track.emergency else 0,
                    on_ground=1 if track.on_ground else 0, now=now))
                lines.append(encode_message(
                    4, icao, gs_kt=track.gs_kt, track_deg=track.track_deg, now=now))
                self.last_pos_emit[icao] = now
            if lines:
                self.sbs.broadcast(lines)

    def run(self):
        self.sbs.start()
        log.info("rid2all %s starting (source %s@%d)",
                 __version__, self.cfg["source"]["device"], self.cfg["source"]["baud"])
        source_thread = threading.Thread(target=self.source.run, daemon=True,
                                         name="mavlink-source")
        source_thread.start()

        last_track_report = 0.0
        while not self._stopping.is_set():
            self._stopping.wait(0.2)
            self._emit()
            now = time.time()
            if now - last_track_report >= 30:
                last_track_report = now
                log.info("tracks: %d live, sbs clients: %d, mode: %s",
                         len(self.tracks.tracks), self.sbs.client_count,
                         self.source.detected_mode())
        self.source.stop()
        self.sbs.stop()
        source_thread.join(timeout=3)
        log.info("rid2all stopped")

    def stop(self):
        self._stopping.set()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="rid2all",
        description="Remote ID to anything: DroneScout radio to SBS/tracking networks")
    parser.add_argument("-c", "--config", help="path to config.toml")
    parser.add_argument("-V", "--version", action="version",
                        version=f"rid2all {__version__}")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    _setup_logging(cfg["log"]["level"])

    daemon = Daemon(cfg)

    def handle_signal(signum, frame):
        log.info("signal %d, shutting down", signum)
        daemon.stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    daemon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
