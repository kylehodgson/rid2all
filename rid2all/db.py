"""readsb aircraft-database export for Remote ID aircraft.

readsb supports --db-file=<csv.gz> (tar1090-db csv format: hex;reg;type;
flags;typelong;year;ownop; — every field INCLUDING the last one terminated
with ';', or the whole entry is discarded), rechecking the file every 30
seconds. tar1090 uses the db fields in aircraft.json to select icons:
Eurocontrol UAS type codes Q1/Q4 map to tar1090's built-in 'uav' marker and
fill the popup's Type code / description / Operator fields.

Note readsb's typeCode field is 3 chars + null (4-char DRON/HRON don't fit),
and registration is 11 chars + null, so the full Remote ID serial goes in
ownOp (64 chars), which tar1090 shows in the details panel.

Because gzip files concatenate transparently, the output can be the Remote ID
entries alone, or the standard tar1090-db aircraft.csv.gz concatenated with
the RID entries (for stations that also receive real aircraft).
"""

import gzip
import hashlib
import logging
import os
import tempfile
import time

log = logging.getLogger("rid2all.db")

# ODID ua_type -> (type code, type long name)
# Q1/Q4: Eurocontrol UAS designators; tar1090 maps them to the 'uav' icon.
_UA_TYPES = {
    2: ("Q4", "Unmanned Rotorcraft (Remote ID)"),  # helicopter/multirotor
}

_DEFAULT = ("Q1", "Unmanned Aircraft (Remote ID)")


def db_line(icao: str, serial: str, ua_type: int | None) -> str:
    type_code, type_long = _UA_TYPES.get(ua_type, _DEFAULT)
    # format: hex;reg;type;flags;typelong;year;ownop; — trailing ';' required
    # (readsb's parser discards the entry otherwise); reg stays empty, the
    # serial goes in ownOp (shown by tar1090, and registration only holds 11
    # chars).
    return ";".join([icao, "", type_code, "00", type_long, "", serial, ""])


def _rid_lines(tracks: dict) -> list[str]:
    lines = []
    for track in tracks.values():
        serial = getattr(track, "rid_serial", None)
        if not serial or not track.icao.startswith("FF"):
            continue
        lines.append(db_line(track.icao, serial, track.ua_type))
    lines.sort()
    return lines


class RIDDbWriter:
    """Writes db csv.gz for readsb: RID entries, optionally concatenated
    onto a base database (the standard tar1090-db aircraft.csv.gz)."""

    def __init__(self, output_path: str, base_path: str = ""):
        self.output_path = output_path
        self.base_path = base_path
        self._base_cache: bytes | None = None
        self._base_mtime = 0.0
        self._last_hash = None
        self._last_attempt = 0.0

    def update(self, tracks: dict) -> bool:
        """Write the db file if the RID entry set changed. Returns wrote."""
        if not self.output_path:
            return False
        now = time.time()
        if now - self._last_attempt < 2.0:
            return False  # debounce
        self._last_attempt = now

        text = "\n".join(_rid_lines(tracks)) + "\n" if tracks else ""
        digest = hashlib.sha1(text.encode()).digest() if text else None
        if digest == self._last_hash:
            return False

        payload = self._rid_gzip(text) if text else b""
        if self.base_path:
            base = self._load_base()
            if base:
                payload = base + payload
        self._atomic_write(payload)
        self._last_hash = digest
        log.info("db file updated: %s (%d rid entries, %d bytes)",
                 self.output_path, len(text.splitlines()) if text else 0, len(payload))
        return True

    def _rid_gzip(self, text: str) -> bytes:
        import io
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as fh:
            fh.write(text.encode("ascii", errors="replace"))
        return buf.getvalue()

    def _load_base(self) -> bytes:
        try:
            mtime = os.path.getmtime(self.base_path)
        except OSError as exc:
            log.warning("db base %s unreadable: %s", self.base_path, exc)
            return None
        if self._base_cache is None or mtime != self._base_mtime:
            try:
                with open(self.base_path, "rb") as fh:
                    self._base_cache = fh.read()
                self._base_mtime = mtime
                log.info("loaded db base %s (%d bytes)", self.base_path,
                         len(self._base_cache))
            except OSError as exc:
                log.warning("db base %s unreadable: %s", self.base_path, exc)
                return None
        return self._base_cache

    def _atomic_write(self, payload: bytes):
        directory = os.path.dirname(self.output_path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".gz.tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            os.replace(tmp, self.output_path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
