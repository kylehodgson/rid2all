# Contributing to rid2all

Thanks for your interest — rid2all is deliberately small (a handful of Python
modules, no third-party runtime dependencies beyond the standard library), so
contributions should stay that way.

## How this repo fits in

rid2all is standalone by design — it speaks SBS over TCP and knows nothing
about WellClear itself. The related repos:

| Repo | What it is | Relation to rid2all |
|---|---|---|
| [wellclear-infra](https://github.com/kylehodgson/wellclear-infra) | Public GPL/AGPL infrastructure for the [WellClear](https://wellclear.aero) community network (readsb hub, mlat-server, tar1090 map) + feeder-facing install docs | Documents how to feed rid2all output into WellClear (`feeder/remote-id.md` there) |
| `wellclear` (private) | WellClear API, stats worker, portal | Consumes SBS pushed from feeders running rid2all |

Bug reports and fixes for the DroneScout→SBS conversion itself belong here.
Questions about WellClear network access, feeder terms, or the map UI belong
in wellclear-infra issues (or contact us via wellclear.aero).

## The one invariant: ICAO synthesis

Drones have no Mode-S address, so rid2all synthesizes an ICAO from the Remote
ID serial using the Bluemark convention: `FF` (an unassigned ICAO prefix) plus
a checksum of the serial. **Anything that changes this mapping breaks every
existing consumer** — drones would appear as new aircraft on every tar1090,
aggregator, and WellClear account. If you think the mapping should change,
open an issue first and make the case; do not submit a PR that alters it
unilaterally. The same drone must map to the same ICAO everywhere, forever.

## Dev setup

Python 3.10+ (3.11+ enables the native `tomllib` path; earlier versions fall
back gracefully):

```sh
git clone https://github.com/kylehodgson/rid2all && cd rid2all
python3 -m venv .venv && . .venv/bin/activate
pip install -e .          # pymavlink is the only runtime dependency
cp config.example.toml config.toml
# edit config.toml: device path + push targets
python -m rid2all
```

Testing without a radio: the `source.device` value is passed straight to
pymavlink's `mavlink_connection`, which also accepts file paths (replay a
captured raw MAVLink stream) and `udp:`/`tcp:` endpoints — so you can develop
and exercise the full parse→track→SBS pipeline without hardware. If you've
got a DroneScout radio, capture a few minutes of both message modes
(OPEN_DRONE_ID_MESSAGE_PACK and ADSB_VEHICLE) before changing the parsers.

## Pull requests

- One logical change per PR; include the reason in the description.
- No new runtime dependencies without an issue first — the zero-dependency
  constraint is what keeps the Docker image small and auditable.
- SBS output changes: verify field semantics against the [SBS spec]
  (http://woodair.net/sbs/article/barebones42_socket_data.htm) — the classic
  gotchas are knots vs m/s and feet vs metres (rid2all handles both; see
  `rid2all/sbs.py`).
- Message-format changes (both DroneScout UART formats are auto-detected per
  message) must keep the other format working — the radio can be switched at
  any time without a daemon restart, and some setups use both.
- Versioning: bump `version` in both `pyproject.toml` and `rid2all/__init__.py`
  for user-visible changes; tags on `main` drive the published image
  (`ghcr.io/kylehodgson/rid2all:<tag>`, multi-arch amd64/arm64/armv7).

## Code style

- Type hints where they clarify; docstrings at module level explaining the
  *why* (see `rid2all/db.py` for the house style — it explains the readsb CSV
  contract in detail).
- Log with `logging.getLogger("rid2all.<module>")`; never `print` outside
  `main.py`'s console-echo path.
