# FOCAS bridge. 32-bit ARM because FANUC's libfwlib32 has no 64-bit ARM build;
# a Pi 5 running 64-bit Raspberry Pi OS runs 32-bit ARM containers natively.
FROM python:3.12-slim-bookworm

# Library source: a copy you got from FANUC (preferred, see lib/README.md), otherwise
# the armv7 build redistributed in Ladder99/fanuc-driver at a pinned commit.
ARG FANUC_DRIVER_COMMIT=b9080d0938757789159714c38305fe559728e146
ARG FWLIB_SHA256=48254b95d21a3f21fcca26bf4936c1e5dea2b65c7427732ae9d5db84f1b0d03a
COPY lib/ /tmp/lib/
RUN set -e; \
    if [ -f /tmp/lib/libfwlib32.so ]; then \
        cp /tmp/lib/libfwlib32.so /usr/local/lib/libfwlib32.so; \
    else \
        python -c "import urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/Ladder99/fanuc-driver/${FANUC_DRIVER_COMMIT}/fanuc/libfwlib32-linux-armv7.so.1.0.5', '/usr/local/lib/libfwlib32.so')"; \
        echo "${FWLIB_SHA256}  /usr/local/lib/libfwlib32.so" | sha256sum -c -; \
    fi; \
    rm -rf /tmp/lib; ldconfig

WORKDIR /app
COPY focas.py bridge.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "bridge.py"]
