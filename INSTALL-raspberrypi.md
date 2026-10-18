# Installing rid2all on raspberrypi.local (alongside an existing ultrafeeder)

Manual steps for the second Pi (`raspberrypi.local`, stack at `/opt/adsb`).
Verified facts about that machine: Docker 27.2, user uid 1000 in `plugdev`
(gid 46), RTL2832U SDR present (real-aircraft station), ultrafeeder already
feeds wellclear.aero. No `ttyACM*` device yet — plug the DS110 in first.

## 1. Get the code

```sh
# from wellclear.local (has the tested copy), or clone once published
rsync -a --exclude .git --exclude .venv \
    kylehodgson@wellclear.local:projects/rid2all/ ~/projects/rid2all/
```

## 2. Create the config and dirs

```sh
sudo mkdir -p /opt/adsb/rid-db
sudo cp ~/projects/rid2all/config.example.toml /opt/adsb/rid2all-config.toml
```

Edit `/opt/adsb/rid2all-config.toml`:

```toml
[source]
device = "/dev/ttyACM0"
baud = 115200

# push into this Pi's ultrafeeder SBS input
[[sbs.push]]
host = "ultrafeeder"
port = 32006

[track]
expire_seconds = 60
position_interval = 1.0
ident_interval = 30.0

# SDR station: keep real-aircraft db lookups working (concatenate)
[db]
output = "/rid-db/db.csv.gz"
base = "/rid-db/base-aircraft.csv.gz"

[log]
level = "info"
```

## 3. Base aircraft db (SDR stations)

```sh
# once ultrafeeder is running; if it isn't yet, skip and add later
sudo docker cp ultrafeeder:/usr/local/share/tar1090/aircraft.csv.gz \
    /opt/adsb/rid-db/base-aircraft.csv.gz
sudo chown 1000:46 /opt/adsb/rid-db/*
```

## 4. Wire the service into /opt/adsb/docker-compose.yml

Add to the **ultrafeeder** service (sudoedit the file):

```yaml
    environment:
      # ... existing env ...
      - READSB_NET_SBS_INPUT_PORT=32006
      - READSB_EXTRA_ARGS=--db-file=/rid-db/db.csv.gz
    volumes:
      # ... existing volumes ...
      - /opt/adsb/rid-db:/rid-db:ro
```

Add rid2all as a new top-level service (published image, no clone needed):

```yaml
  rid2all:
    image: ghcr.io/kylehodgson/rid2all:latest
    container_name: rid2all
    restart: unless-stopped
    # host user owning ./rid-db + plugdev gid for the tty
    user: "1000:46"
    devices:
      - /dev/ttyACM0:/dev/ttyACM0
    volumes:
      - /opt/adsb/rid2all-config.toml:/data/config.toml:ro
      - /opt/adsb/rid-db:/rid-db
```

(To build from a local checkout instead, replace `image:` with
`build: /home/kylehodgson/projects/rid2all`.)

Notes:
- `/dev/ttyACM0` — verify with `ls /dev/ttyACM*` after plugging the DS110 in.
- If this stack already sets `READSB_EXTRA_ARGS` for something else, append
  the `--db-file` flag to the existing value instead of adding a second var.
- `user: "1000:46"`: the rid-db dir must be writable by uid 1000 (chown above).

## 5. Bring it up

```sh
cd /opt/adsb
sudo docker compose up -d --build rid2all
sudo docker compose up -d ultrafeeder   # recreate to pick up new env
sudo docker logs -f rid2all
```

## 6. Verify

```sh
# drone on the map (Q4 = rotorcraft; upstream tar1090 shows the fixed-wing
# 'uav' icon until the multicopter patch lands upstream)
curl -s http://localhost:8080/data/aircraft.json | python3 -m json.tool | grep -A2 ff59

# db file contains RID entries
zcat /opt/adsb/rid-db/db.csv.gz | grep ^FF
```

## Notes specific to this machine

- This Pi **already feeds wellclear.aero** via readsb
  (`ULTRAFEEDER_CONFIG=adsb,feed.wellclear.aero,30004,beast_reduce_plus_out…`).
  Because rid2all's SBS is merged into readsb's aircraft table, drones seen
  here will also reach wellclear.aero on the existing beast feed — the hub's
  dedicated SBS ingest (TBP) just makes the Remote ID provenance explicit.
- tar1090 on this Pi is stock (no multicopter icon patch); Q4 falls back to
  the upstream 'uav' fixed-wing-drone icon. The patch lives in
  `~/tar1090` on wellclear.local; apply the same two-file change here when
  convenient, or wait for the upstream PR.
- `/opt/adsb` is root-owned: edits need sudo; rid-db needs the chown in
  step 3 so rid2all (uid 1000) can write `db.csv.gz`.
