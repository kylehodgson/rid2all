"""SBS BaseStation output: encoder, TCP listeners, and push connectors.

Emits the standard BaseStation text protocol (MSG,1 / MSG,3 / MSG,4) so any
dump1090-family consumer can ingest it: readsb (--net-sbs-in-port),
ultrafeeder, tar1090 behind readsb, and SBS-ingesting aggregators
(wellclear.aero, adsb.lol, ...).
"""

import logging
import queue
import socket
import threading
import time

log = logging.getLogger("rid2all.sbs")

_MS_PER_DAY = 86400000


def _sbs_timestamp(now: float | None = None) -> tuple[str, str]:
    """Return (date,time) strings in BaseStation format."""
    if now is None:
        now = time.time()
    millis = int(now * 1000)
    days, rest = divmod(millis, _MS_PER_DAY)
    h, rest = divmod(rest, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    base = time.gmtime(millis / 1000.0)
    date = time.strftime("%Y/%m/%d", base)
    return date, f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def encode_message(msg_type: int, icao: str, callsign: str = "",
                   alt_ft: float | None = None, gs_kt: float | None = None,
                   track_deg: float | None = None, lat: float | None = None,
                   lon: float | None = None, vr_fpm: float | None = None,
                   squawk: str | None = None, alert: int = 0,
                   emergency: int = 0, on_ground: int = 0,
                   spi: int = 0, now: float | None = None) -> str:
    """Build one SBS line. Field indices follow the BaseStation spec:
    0 type, 1 subtype, 2 session, 3 aircraft, 4 icao, 5 flightid,
    6-9 timestamps, 10 callsign, 11 alt, 12 gs, 13 track, 14 lat, 15 lon,
    16 vr, 17 squawk, 18 alert, 19 emergency, 20 spi, 21 on-ground.
    """
    fields = [""] * 22
    fields[0] = "MSG"
    fields[1] = str(msg_type)
    fields[4] = icao
    date, clock = _sbs_timestamp(now)
    fields[6] = date
    fields[7] = clock
    fields[8] = date
    fields[9] = clock
    fields[10] = callsign
    if msg_type in (2, 3):
        if alt_ft is not None:
            fields[11] = str(round(alt_ft))
    if msg_type in (2, 4):
        if gs_kt is not None:
            fields[12] = str(round(gs_kt))
        if track_deg is not None:
            fields[13] = str(round(track_deg))
    if msg_type in (2, 3):
        if lat is not None:
            fields[14] = f"{lat:.5f}"
        if lon is not None:
            fields[15] = f"{lon:.5f}"
        if vr_fpm is not None:
            fields[16] = str(round(vr_fpm))
    if squawk:
        fields[17] = squawk
    fields[18] = str(alert)
    fields[19] = str(emergency)
    fields[20] = str(spi)
    fields[21] = str(on_ground)
    return ",".join(fields)


class _Client:
    """One SBS consumer: a queue plus a writer thread, push or serve mode."""

    def __init__(self, name: str, sock: socket.socket | None):
        self.name = name
        self.sock = sock
        self.queue: queue.Queue[str | None] = queue.Queue(maxsize=1000)
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name=f"sbs-{name}")
        self.thread.start()

    def _run(self):
        try:
            while True:
                line = self.queue.get()
                if line is None:
                    break
                self.sock.sendall(line.encode("ascii"))
        except OSError as exc:
            log.info("sbs client %s disconnected: %s", self.name, exc)
        finally:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass

    def send(self, line: str):
        try:
            self.queue.put_nowait(line + "\n")
        except queue.Full:
            log.warning("sbs client %s too slow, dropping", self.name)

    def close(self):
        self.queue.put(None)

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()


class SBSManager:
    """Owns listeners, push connectors, and broadcasts messages to all."""

    def __init__(self, listen_targets: list[dict], push_targets: list[dict]):
        self._listen_targets = listen_targets
        self._push_targets = push_targets
        self._clients: list[_Client] = []
        self._lock = threading.Lock()
        self._servers: list[socket.socket] = []
        self._stopping = threading.Event()

    def start(self):
        for target in self._listen_targets:
            thread = threading.Thread(target=self._serve, args=(target,), daemon=True,
                                      name=f"sbs-listen-{target['port']}")
            thread.start()
        for target in self._push_targets:
            thread = threading.Thread(target=self._push_loop, args=(target,), daemon=True,
                                      name=f"sbs-push-{target['host']}:{target['port']}")
            thread.start()

    def stop(self):
        self._stopping.set()
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            client.close()

    @property
    def client_count(self) -> int:
        with self._lock:
            return sum(1 for c in self._clients if c.alive)

    def _serve(self, target: dict):
        bind = target.get("bind", "0.0.0.0")
        port = target["port"]
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((bind, port))
            server.listen(4)
            self._servers.append(server)
        except OSError as exc:
            log.error("cannot listen on %s:%s: %s", bind, port, exc)
            return
        log.info("sbs listening on %s:%s", bind, port)
        while not self._stopping.is_set():
            try:
                server.settimeout(1.0)
                sock, addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            name = f"{addr[0]}:{addr[1]}"
            client = _Client(name, sock)
            with self._lock:
                self._clients.append(client)
            log.info("sbs client connected %s", name)

    def _push_loop(self, target: dict):
        host, port = target["host"], target["port"]
        delay = 1.0
        while not self._stopping.is_set():
            try:
                sock = socket.create_connection((host, port), timeout=10)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError as exc:
                log.warning("sbs push %s:%s failed (%s), retry in %.0fs",
                            host, port, exc, delay)
                self._stopping.wait(delay)
                delay = min(delay * 2, 60)
                continue
            log.info("sbs push connected %s:%s", host, port)
            delay = 1.0
            client = _Client(f"push-{host}:{port}", sock)
            with self._lock:
                self._clients.append(client)
            # Wait until the client dies or we're stopping, then reconnect.
            while client.alive and not self._stopping.is_set():
                self._stopping.wait(1.0)
            client.close()
            with self._lock:
                if client in self._clients:
                    self._clients.remove(client)
            if not self._stopping.is_set():
                log.info("sbs push %s:%s lost, reconnecting", host, port)

    def broadcast(self, lines: list[str]):
        if not lines:
            return
        with self._lock:
            clients = list(self._clients)
        for line in lines:
            for client in clients:
                client.send(line)
