"""Configuration loading for rid2all (TOML)."""

import logging
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None

log = logging.getLogger("rid2all.config")

DEFAULTS = {
    "source": {"device": "/dev/ttyACM0", "baud": 115200},
    "sbs": {"listen": [], "push": []},
    "track": {
        "expire_seconds": 60,
        "position_interval": 1.0,
        "ident_interval": 30.0,
    },
    "db": {"output": "", "base": ""},
    "log": {"level": "info"},
}


def _merge(base, extra):
    out = dict(base)
    for key, value in extra.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path=None):
    """Load config from a TOML file, falling back to DEFAULTS."""
    cfg = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULTS.items()}
    if path:
        if tomllib is None:
            raise RuntimeError("config files need Python 3.11+ (tomllib)")
        with open(path, "rb") as fh:
            cfg = _merge(cfg, tomllib.load(fh))
    _validate(cfg)
    return cfg


def _validate(cfg):
    if not cfg["source"].get("device"):
        raise ValueError("source.device is required")
    if not cfg["sbs"]["listen"] and not cfg["sbs"]["push"]:
        raise ValueError("at least one sbs.listen or sbs.push target is required")
    for target in cfg["sbs"]["listen"]:
        if not target.get("port"):
            raise ValueError("sbs.listen entries need a port")
    for target in cfg["sbs"]["push"]:
        if not (target.get("host") and target.get("port")):
            raise ValueError("sbs.push entries need host and port")
