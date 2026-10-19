# Pinned base: SMB1 is deprecated upstream, so Samba is only upgraded deliberately.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends samba samba-vfs-modules \
 && rm -rf /var/lib/apt/lists/* /etc/samba/smb.conf

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
