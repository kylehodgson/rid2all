"""OpenDroneID message-pack and MAVLink ADSB_VEHICLE parsing.

The ODID pack parser is adapted from Bluemark Innovations'
RemoteID-DroneScout-Bridge-Python (MIT), rewritten with `struct` instead of
`bitstruct` and returning structured dicts instead of printing.
"""

import struct

# ODID wire constants
ODID_MESSAGE_SIZE = 25

# BasicID id_type values
ID_TYPE_SERIAL = 1

# Location status values
STATUS_UNDECLARED = 0
STATUS_GROUND = 1
STATUS_AIRBORNE = 2
STATUS_EMERGENCY = 3
STATUS_SYSTEM_FAILURE = 4

_INVALID_ALT_RAW = 1000  # 0xFFFF encoded altitude sentinel check handled below


def _clean_ascii(raw: bytes) -> str:
    """Decode an ODID fixed-width ASCII field, dropping padding/garbage."""
    text = raw.decode("ascii", errors="replace")
    return text.replace(" ", "").replace("\t", "").replace("\n", "").replace("\r", "")


def _alt_from_raw(raw: int) -> float | None:
    """ODID altitudes are (raw - 2000) / 2 metres; 0xFFFF means invalid."""
    if raw == 0xFFFF:
        return None
    return (raw - 2000) / 2.0


def parse_basic_id(msg: bytes) -> dict:
    return {
        "id_type": msg[1] >> 4,
        "ua_type": msg[1] & 0x0F,
        "uas_id": _clean_ascii(msg[2:22]),
    }


def parse_location(msg: bytes) -> dict:
    flags = msg[1]
    speed_mult = flags & 0x01
    speed_enc = msg[3]
    speed_vert_raw = msg[4]

    lat_raw, lon_raw = struct.unpack_from("<ii", msg, 5)
    alt_baro_raw, alt_geo_raw, height_raw = struct.unpack_from("<HHH", msg, 13)

    if speed_enc == 255:
        speed_hor = None
    elif speed_mult:
        speed_hor = (speed_enc * 0.75) + (255 * 0.25)
    else:
        speed_hor = speed_enc * 0.75

    speed_vert = None if speed_vert_raw == 63 else speed_vert_raw * 0.5

    direction = msg[2]
    if direction > 360 or direction < 0:
        direction = None

    return {
        "status": (flags >> 4) & 0x0F,
        "height_type": (flags >> 2) & 0x01,
        "track_deg": direction,
        "speed_hor_ms": speed_hor,
        "speed_vert_ms": speed_vert,
        "lat": lat_raw / 1e7,
        "lon": lon_raw / 1e7,
        "alt_baro_m": _alt_from_raw(alt_baro_raw),
        "alt_geo_m": _alt_from_raw(alt_geo_raw),
        "height_m": _alt_from_raw(height_raw),
    }


def parse_system(msg: bytes) -> dict:
    flags = msg[1]
    op_lat_raw, op_lon_raw = struct.unpack_from("<ii", msg, 2)
    (area_count,) = struct.unpack_from("<H", msg, 10)
    area_radius = msg[12]
    (area_ceiling_raw,) = struct.unpack_from("<h", msg, 13)
    (area_floor_raw,) = struct.unpack_from("<h", msg, 15)
    (op_alt_raw,) = struct.unpack_from("<h", msg, 18)

    def signed_alt(raw):
        if raw in (-1000, 1000):
            return None
        return (raw - 2000) / 2.0

    return {
        "classification_type": (flags >> 2) & 0x03,
        "operator_location_type": flags & 0x03,
        "operator_lat": op_lat_raw / 1e7,
        "operator_lon": op_lon_raw / 1e7,
        "area_count": area_count,
        "area_radius_m": area_radius,
        "area_ceiling_m": signed_alt(area_ceiling_raw),
        "area_floor_m": signed_alt(area_floor_raw),
        "ua_category": (msg[17] >> 4) & 0x0F,
        "ua_class": msg[17] & 0x0F,
        "operator_alt_m": signed_alt(op_alt_raw),
    }


def parse_operator_id(msg: bytes) -> dict:
    return {"type": msg[1], "operator_id": _clean_ascii(msg[2:22])}


def parse_self_id(msg: bytes) -> dict:
    text = msg[2:25].decode("ascii", errors="replace")
    return {
        "type": msg[1],
        "text": text.replace("\t", "").replace("\n", "").replace("\r", "").rstrip(),
    }


def parse_message_pack(payload: bytes, size: int) -> dict | None:
    """Parse an OPEN_DRONE_ID_MESSAGE_PACK into a flat aircraft snapshot.

    Returns None when the pack contains no usable identity (no BasicID).
    """
    size = min(size, len(payload) // ODID_MESSAGE_SIZE)
    if size == 0:
        return None

    basic_ids = []
    location = None
    system = None
    operator_id = None
    self_id = None

    for i in range(size):
        msg = payload[i * ODID_MESSAGE_SIZE : (i + 1) * ODID_MESSAGE_SIZE]
        msg_type = msg[0] >> 4
        if msg_type == 0:
            basic_ids.append(parse_basic_id(msg))
        elif msg_type == 1:
            location = parse_location(msg)
        elif msg_type == 4:
            system = parse_system(msg)
        elif msg_type == 5:
            operator_id = parse_operator_id(msg)
        elif msg_type == 3:
            self_id = parse_self_id(msg)

    # Prefer a serial-number BasicID; fall back to the first BasicID present.
    ident = None
    for basic in basic_ids:
        if basic["id_type"] == ID_TYPE_SERIAL and basic["uas_id"]:
            ident = basic
            break
    if ident is None and basic_ids:
        ident = basic_ids[0]
    if ident is None or not ident["uas_id"]:
        return None

    return {
        "id": ident["uas_id"],
        "id_type": ident["id_type"],
        "ua_type": ident["ua_type"],
        "location": location,
        "system": system,
        "operator_id": operator_id,
        "self_id": self_id,
    }


def parse_adsb_vehicle(msg) -> dict:
    """Flatten a MAVLink ADSB_VEHICLE message."""
    callsign = (msg.callsign or "").strip()
    heading = msg.heading / 100.0 if msg.heading != 0x7FFF else None
    return {
        "icao": "%06X" % (msg.ICAO_address & 0xFFFFFF),
        "callsign": callsign,
        "lat": msg.lat,
        "lon": msg.lon,
        "alt_m": msg.altitude,
        "altitude_type": msg.altitude_type,  # 0 baro, 1 GNSS
        "speed_hor_ms": None if msg.hor_velocity == 0xFFFF else msg.hor_velocity / 100.0,
        "speed_vert_ms": None if msg.ver_velocity == 0x7FFF else msg.ver_velocity / 100.0,
        "track_deg": heading,
        "squawk": str(msg.squawk) if msg.squawk else None,
    }
