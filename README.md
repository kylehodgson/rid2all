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
DroneScout DS110/DS220/DB120 ──MAVLink (USB serial)──▶ rid2all ──SBS──▶ readsb ──▶ tar1090 / networks
                                                      │
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

## Quick start (Raspberry Pi + DroneScout over USB)

```sh
sudo apt install python3-venv
sudo useradd -r -M -G plugdev rid2all          # plugdev owns /dev/ttyACM*
sudo mkdir -p /opt/rid2all /etc/rid2all
sudo cp -r . /opt/rid2all/src
cd /opt/rid2all/src
sudo python3 -m venv venv
sudo venv/bin/pip install .                    # pulls pymavlink
sudo cp config.example.toml /etc/rid2all/config.toml
sudo cp systemd/rid2all.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now rid2all
journalctl -u rid2all -f
```

Verify:

```sh
# watch the SBS stream (drone must be transmitting Remote ID in range)
nc localhost 30003
```

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
