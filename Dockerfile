FROM python:3.13-slim

# pymavlink pulls no compiled deps; nothing else needed at runtime.
COPY pyproject.toml README.md LICENSE /src/
COPY rid2all /src/rid2all
COPY config.example.toml /app/config.example.toml
RUN pip install --no-cache-dir /src && rm -rf /src

# Run as non-root; the host passes the device in and sets GROUP_ADD to
# whatever owns /dev/ttyACM* (plugdev on Raspberry Pi OS).
RUN useradd -r -s /usr/sbin/nologin rid2all
USER rid2all

STOPSIGNAL SIGTERM
# If /data/config.toml is missing (fresh install), fall back to the bundled
# example so the container starts instead of crash-looping; see README.
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["-c", "/data/config.toml"]
