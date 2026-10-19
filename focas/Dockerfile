# FOCAS bridge. FANUC's libfwlib32 exists for 32-bit ARM, 32-bit x86 and x86-64 Linux,
# but not for 64-bit ARM: on a Pi (arm64) this image is built for linux/arm/v7, which a
# Pi 5 running 64-bit Raspberry Pi OS runs natively. On an x86-64 host it is built for
# linux/amd64. host/install.sh picks the platform (FOCAS_PLATFORM in .env).
FROM python:3.12-slim-bookworm

# Library source: a copy you got from FANUC (preferred, see lib/README.md), otherwise
# the matching build redistributed in Ladder99/fanuc-driver at a pinned commit.
ARG TARGETARCH
ARG FANUC_DRIVER_COMMIT=b9080d0938757789159714c38305fe559728e146
COPY lib/ /tmp/lib/
RUN set -e; \
    case "$TARGETARCH" in \
        arm)   file=libfwlib32-linux-armv7.so.1.0.5; sha=48254b95d21a3f21fcca26bf4936c1e5dea2b65c7427732ae9d5db84f1b0d03a; machine=40 ;; \
        amd64) file=libfwlib32-linux-x64.so.1.0.5;   sha=0304931e1366e57ff19f42021f84ac2cb694b0b2c764e0b99d81317ee0880d84; machine=62 ;; \
        386)   file=libfwlib32-linux-x86.so.1.0.5;   sha=fbab3f351b00c5c9da1d0e2e9978bb3bf1a97a46150e8e15538bccd0596feeb4; machine=3 ;; \
        *) echo "FANUC has no FOCAS library for '$TARGETARCH'. Build for linux/arm/v7 (Pi) or linux/amd64 (PC): set FOCAS_PLATFORM in .env." >&2; exit 1 ;; \
    esac; \
    if [ -f /tmp/lib/libfwlib32.so ]; then \
        cp /tmp/lib/libfwlib32.so /usr/local/lib/libfwlib32.so; \
    else \
        python -c "import urllib.request as u, sys; u.urlretrieve(sys.argv[1], '/usr/local/lib/libfwlib32.so')" \
            "https://raw.githubusercontent.com/Ladder99/fanuc-driver/${FANUC_DRIVER_COMMIT}/fanuc/${file}"; \
        echo "${sha}  /usr/local/lib/libfwlib32.so" > /tmp/fwlib.sha256; \
        sha256sum -c /tmp/fwlib.sha256; rm /tmp/fwlib.sha256; \
    fi; \
    # The library must be built for this image's CPU (ELF e_machine: 40 ARM, 3 x86, 62 x86-64)
    python -c "import struct, sys; h = open('/usr/local/lib/libfwlib32.so', 'rb').read(20); \
m = struct.unpack('<H', h[18:20])[0] if h[:4] == b'\x7fELF' else -1; \
cpu = {40: '32-bit ARM', 3: '32-bit x86', 62: 'x86-64'}; \
sys.exit(0 if m == int(sys.argv[1]) else 'libfwlib32.so is for %s, but this image is %s: use the matching build' % (cpu.get(m, 'an unknown CPU'), cpu[int(sys.argv[1])]))" "$machine"; \
    rm -rf /tmp/lib; ldconfig

# Debian security updates; pip/setuptools aren't needed at runtime (the bridge uses only the stdlib).
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
 && pip uninstall -y setuptools wheel pip

WORKDIR /app
COPY focas.py bridge.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "bridge.py"]
