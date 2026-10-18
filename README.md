# rid2all

**Remote ID to anything.** A small daemon that reads drone Remote ID from
[DroneScout](https://dronescout.co/) radios and republishes it as
[SBS BaseStation](http://woodair.net/sbs/article/barebones42_socket_data.htm)
so it lands in the same pipelines as classic ADS-B: `readsb`, `ultrafeeder`,
`tar1090`, MLAT, and community aggregators — starting with
[wellclear.aero](https://wellclear.aero).

It complements the open-source ADS-B stack rather than replacing it: your
1090MHz feed keeps flowing through readsb/ultrafeeder exactly as before, and
rid2all adds drones as a second receiver source.

```
DroneScout DS110/DS220/DB120 ──MAVLink (USB serial)──▶ rid2all ──SBS──▶ ultrafeeder ──▶ tar1090 / networks
                                                      │                        └──beast──▶ aggregators
                                                      └──SBS push──▶ feed.wellclear.aero (etc.)
```

## Features

- **Both DroneScout output formats**, auto-detected per message:
  `MAVLink OPEN_DRONE_ID_MESSAGE_PACK` (Remote ID) and
  `MAVLink ADSB_VEHICLE` — switch the radio's UART message type any time,
  no daemon restart needed.
- **Multiple drones at once** — one track table keyed by Remote ID serial
  (or ADS-B ICAO), with expiry and per-aircraft cadence.
- **Standard SBS output**: serve on a TCP port (`sbs.listen`) and/or push to
  remote aggregators (`sbs.push`), with automatic reconnect.
- **Bridge-compatible ICAO synthesis**: drones have no Mode-S address, so
  rid2all uses the Bluemark convention — `FF` (an unassigned ICAO prefix) plus
  a checksum of the Remote ID serial — so the same drone maps to the same
  ICAO everywhere.
- Correct SBS field semantics (speed in **knots**, altitude in **feet**,
  GNSS altitude preferred, ODID ground/emergency status mapped to SBS flags).

## Quick start (Raspberry Pi + DroneScout over USB, Docker)

```sh
git clone https://github.com/wellclear/rid2all && cd rid2all
cp config.example.toml config.toml
# edit config.toml: device path + push targets

# the host group that owns the radio's serial device (usually plugdev)
echo RID2ALL_TTY_GID=$(stat -c %g /dev/ttyACM0) >> .env

docker compose up -d --build

docker compose logs -f
```

Verify:

```sh
# watch the SBS stream (drone must be transmitting Remote ID in range)
nc localhost 30003
```

Docker notes:

- The container runs non-root; `group_add` gives it access to
  `/dev/ttyACM0` via the host group that owns the device.

## Integrating with ultrafeeder (docker compose)

rid2all is designed as a drop-in sidecar for an
[ultrafeeder](https://github.com/sdr-enthusiasts/docker-adsb-ultrafeeder)
stack: paste the service from `compose-examples/ultrafeeder-service.yml`
into your stack's `docker-compose.yml` and use the matching config from
`compose-examples/ultrafeeder-config.toml`. Both documented ultrafeeder
ingest paths are supported:

**Option A — rid2all pushes into ultrafeeder's SBS input port** (all in the
ultrafeeder docs): in `config.toml`, push to `ultrafeeder:32006`, and on the
ultrafeeder service set:

```yaml
- READSB_NET_SBS_INPUT_PORT=32006
```

**Option B — ultrafeeder connects to rid2all's listener**: keep `sbs.listen`
on 30003 (the default) and on the ultrafeeder service set:

```yaml
- READSB_NET_CONNECTOR=rid2all,30003,sbs_in
```

(`sbs_in` is verified in upstream wiedehopf/readsb `help.h`; ultrafeeder
passes `READSB_NET_CONNECTOR` straight through to readsb. Option A stays
entirely within ultrafeeder's documented table, so prefer it if you want
documented-only configuration.)

Either way, drones appear on tar1090 next to aeroplanes, tagged with the
synthesized `FFxxxx` ICAO and the manufacturer-code callsign, and get
included in whatever the stack feeds downstream (including your WellClear
feed over beast).

## Other integrations

### plain readsb (bare metal)

Add an SBS input to `/etc/default/readsb` (append to `NET_OPTIONS`):

```
--net-sbs-in-port 30006
```

then restart readsb, and set rid2all's `sbs.push` to `127.0.0.1:30006`
(or add a `--net-connector=<rid2all-host>,30003,sbs_in` line).

### Pushing straight to aggregators

Any SBS-ingesting network works via `sbs.push` with automatic reconnect —
no local readsb needed. wellclear.aero's ingest port will be published in
the feeder docs.

## Running bare-metal instead

`systemd/rid2all.service` + `pip install .` works too; see the unit file
for the expected paths (`/etc/rid2all/config.toml`, venv at `/opt/rid2all`).

## Wiring it into the ADS-B stack

### plain readsb

Add an SBS input to `/etc/default/readsb` (append to `NET_OPTIONS`):

```
--net-sbs-in-port 30006
```

then restart readsb. With rid2all serving on port 30003, point a
`--net-sbs-in` connector at it or run rid2all's `sbs.push` at
`127.0.0.1:30006`.

### ultrafeeder / docker-based stacks

The daemon's SBS output is just another SBS source: add it as an SBS input
port to the stack's readsb, or run rid2all on the host with `sbs.push`
pointing at the container's SBS input port. tar1090's map then shows drones
next to aeroplanes, tagged by the synthesized `FFxxxx` ICAO and callsign.

### wellclear.aero

Set a push target once your feeder account exists:

```toml
[[sbs.push]]
host = "feed.wellclear.aero"
port = 30006   # Remote ID / SBS ingest port — see the WellClear feeder docs
```

Remote ID feeds and 1090MHz feeds are merged per-aircraft by the network.

## ODID → SBS mapping

| ODID field | SBS usage |
|---|---|
| BasicID `UAS ID` (serial preferred) | ICAO = `FF` + checksum(serial); callsign = first 4 + last 4 chars |
| Location `latitude/longitude` | MSG,3 lat/lon |
| `altitude geometric` (fallback: barometric) | MSG,3 altitude (m→ft) |
| `speed horizontal` | MSG,4 groundspeed (m/s→kt) |
| `direction` | MSG,4 track |
| `speed vertical` | MSG,3 vertical rate (m/s→ft/min) |
| `status`: ground / airborne / emergency / system failure | SBS on-ground / emergency flags |
| MAVLink `ADSB_VEHICLE` (ICAO, callsign, squawk, …) | mapped directly |

Message cadence: MSG,1 ident on first sight / callsign change / every 30 s;
MSG,3 + MSG,4 every second per aircraft while the radio keeps it fresh.

## Config reference

See `config.example.toml` — source device/baud, SBS listen + push targets,
track expiry and emit cadence, log level.

## Licenses & credits

- rid2all is MIT licensed. The ODID message-pack parser is adapted from
  [Bluemark Innovations' RemoteID-DroneScout-Bridge-Python](https://github.com/BluemarkInnovations/RemoteID-DroneScout-Bridge-Python)
  (MIT) — thank you, Bluemark.
- Works alongside: [readsb](https://github.com/wiedehopf/readsb),
  [tar1090](https://github.com/wiedehopf/tar1090),
  [ultrafeeder](https://github.com/sdr-enthusiasts/docker-readsb-protobuf),
  [wellclear-infra](https://github.com/wellclear/wellclear-infra).
