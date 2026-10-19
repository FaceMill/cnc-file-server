FROM python:3.12-slim-bookworm

# nmcli talks to the host's NetworkManager over the mounted D-Bus socket;
# nft and ip operate on the host because the container uses host networking.
RUN apt-get update && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends network-manager nftables iproute2 iputils-ping \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# pip and setuptools are only needed to build; removing them keeps their bundled
# (and often outdated) libraries out of the running image.
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y setuptools wheel pip
COPY cncadmin ./cncadmin

# One worker so the pending-network-change state lives in one process.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "127.0.0.1:8090", \
     "--access-logfile", "-", "cncadmin:create_app()"]
