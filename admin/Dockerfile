FROM python:3.12-slim-bookworm

# nmcli talks to the host's NetworkManager over the mounted D-Bus socket;
# nft and ip operate on the host because the container uses host networking.
RUN apt-get update \
 && apt-get install -y --no-install-recommends network-manager nftables iproute2 iputils-ping \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY cncadmin ./cncadmin

# One worker so the pending-network-change state lives in one process.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "127.0.0.1:8090", \
     "--access-logfile", "-", "cncadmin:create_app()"]
