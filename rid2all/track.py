"""Track state: turn incoming ODID/ADS-B snapshots into stable per-aircraft tracks.

ICAO generation follows the Bluemark DroneScout bridge convention so the same
drone maps to the same ICAO across networks: 'FF' (unassigned prefix) + two
checksum bytes derived from the Remote ID serial number.
"""

import logging
import time

from . import odid

log = logging.getLogger("rid2all.track")

MS_TO_KNOTS = 1.943844
MS_TO_FPM = 196.850394
M_TO_FT = 3.280840


def icao_from_id(uas_id: str) -> str:
    """Bridge-compatible ICAO synthesis: FF + XOR checksum of the hex string."""
    data_hex = uas_id.encode("utf-8").hex()
    checksum1 = 0
    checksum2 = 0
    for char in data_hex:
        checksum1 ^= ord(char)
        checksum2 ^= checksum1
    return "FF%02X%02X" % (checksum1, checksum2)


def callsign_from_id(uas_id: str) -> str:
    """SBS callsign: first 4 chars (manufacturer code) + last 4 of the ID."""
    if len(uas_id) > 8:
        return (uas_id[:4] + uas_id[-4:]).ljust(8)
    return uas_id.ljust(8)


class Track:
    __slots__ = (
        "icao", "callsign", "lat", "lon", "alt_ft", "alt_is_geo",
        "gs_kt", "track_deg", "vr_fpm", "on_ground", "emergency",
        "squawk", "ua_type", "last_seen", "last_ident", "new",
    )

    def __init__(self, icao: str, now: float):
        self.icao = icao
        self.callsign = ""
        self.lat = None
        self.lon = None
        self.alt_ft = None
        self.alt_is_geo = False
        self.gs_kt = None
        self.track_deg = None
        self.vr_fpm = None
        self.on_ground = False
        self.emergency = False
        self.squawk = None
        self.ua_type = None
        self.last_seen = now
        self.last_ident = 0.0
        self.new = True


class TrackTable:
    def __init__(self, expire_seconds: float = 60.0):
        self.expire_seconds = expire_seconds
        self.tracks: dict[str, Track] = {}

    def _track(self, icao: str, now: float) -> Track:
        track = self.tracks.get(icao)
        if track is None:
            track = Track(icao, now)
            self.tracks[icao] = track
            log.info("new track %s", icao)
        return track

    def update_odid(self, snapshot: dict, now: float | None = None):
        """Apply an ODID message-pack snapshot (see odid.parse_message_pack)."""
        now = time.time() if now is None else now
        track = self._track(icao_from_id(snapshot["id"]), now)
        track.last_seen = now
        track.new = track.callsign != callsign_from_id(snapshot["id"])

        callsign = callsign_from_id(snapshot["id"])
        if callsign != track.callsign:
            log.info("%s callsign %s -> %s", track.icao, track.callsign.strip() or "?", callsign.strip())
            track.callsign = callsign

        track.ua_type = snapshot["ua_type"]

        location = snapshot["location"]
        if location:
            track.lat = location["lat"]
            track.lon = location["lon"]
            status = location["status"]
            track.emergency = status in (odid.STATUS_EMERGENCY, odid.STATUS_SYSTEM_FAILURE)
            track.on_ground = status == odid.STATUS_GROUND

            # Prefer GNSS altitude; fall back to baro.
            if location["alt_geo_m"] is not None:
                track.alt_ft = location["alt_geo_m"] * M_TO_FT
                track.alt_is_geo = True
            elif location["alt_baro_m"] is not None:
                track.alt_ft = location["alt_baro_m"] * M_TO_FT
                track.alt_is_geo = False

            if location["speed_hor_ms"] is not None:
                track.gs_kt = location["speed_hor_ms"] * MS_TO_KNOTS
            if location["track_deg"] is not None:
                track.track_deg = float(location["track_deg"])
            if location["speed_vert_ms"] is not None:
                track.vr_fpm = location["speed_vert_ms"] * MS_TO_FPM

        system = snapshot["system"]
        if system and system["operator_alt_m"] is not None and track.alt_ft is None:
            track.alt_ft = system["operator_alt_m"] * M_TO_FT

    def update_adsb_vehicle(self, snapshot: dict, now: float | None = None):
        """Apply a MAVLink ADSB_VEHICLE snapshot (see odid.parse_adsb_vehicle)."""
        now = time.time() if now is None else now
        icao = snapshot["icao"]
        track = self._track(icao, now)
        track.last_seen = now

        callsign = (snapshot["callsign"] or "").ljust(8)[:8]
        if callsign and callsign != track.callsign:
            log.info("%s callsign %s -> %s", icao, track.callsign.strip() or "?", callsign.strip())
            track.callsign = callsign

        track.lat = snapshot["lat"]
        track.lon = snapshot["lon"]
        if snapshot["alt_m"] is not None:
            track.alt_ft = snapshot["alt_m"] * M_TO_FT
            track.alt_is_geo = snapshot["altitude_type"] == 1
        if snapshot["speed_hor_ms"] is not None:
            track.gs_kt = snapshot["speed_hor_ms"] * MS_TO_KNOTS
        if snapshot["track_deg"] is not None:
            track.track_deg = snapshot["track_deg"]
        if snapshot["speed_vert_ms"] is not None:
            track.vr_fpm = snapshot["speed_vert_ms"] * MS_TO_FPM
        if snapshot["squawk"]:
            track.squawk = snapshot["squawk"]

    def expire(self, now: float | None = None):
        now = time.time() if now is None else now
        expired = [icao for icao, track in self.tracks.items()
                   if now - track.last_seen > self.expire_seconds]
        for icao in expired:
            log.info("track expired %s", icao)
            del self.tracks[icao]
        return expired
