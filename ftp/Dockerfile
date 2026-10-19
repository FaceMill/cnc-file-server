FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends vsftpd procps \
 && rm -rf /var/lib/apt/lists/* \
 && echo /usr/sbin/nologin >> /etc/shells

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
