FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587 AS build
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends ca-certificates && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends git build-essential cmake gperf libssl-dev zlib1g-dev && rm -rf /var/lib/apt/lists/*
# Official source commit, queried 2026-10-20. Submodule commits are pinned by it.
ARG TELEGRAM_BOT_API_REV=e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1
RUN git init /src && cd /src && git remote add origin https://github.com/tdlib/telegram-bot-api.git && git fetch --depth 1 origin "$TELEGRAM_BOT_API_REV" && git checkout --detach FETCH_HEAD && git submodule update --init --recursive --depth 1
COPY deploy/bot-api/native-proxy.patch /native-proxy.patch
RUN cd /src && git apply --check /native-proxy.patch && git apply /native-proxy.patch && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/out && cmake --build build --parallel 2 && cmake --install build
COPY deploy/bot-api/native-progress.patch /native-progress.patch
COPY deploy/bot-api/ProgressCache.h /src/telegram-bot-api/ProgressCache.h
RUN cd /src && git apply --check /native-progress.patch && git apply /native-progress.patch && cmake --build build --parallel 2 && cmake --install build
RUN mkdir -p /out/licenses/tdlib && cp /src/LICENSE* /out/licenses/ && cp /src/td/LICENSE* /out/licenses/tdlib/

FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
LABEL org.opencontainers.image.version="0.1.0"
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends ca-certificates libssl3 zlib1g libstdc++6 python3 python3-cryptography && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/ /usr/local/
COPY deploy/bot-api/entrypoint.sh /entrypoint.sh
COPY deploy/bot-api/supervisor.py /usr/local/lib/tgd-api-supervisor.py
COPY deploy/bot-api/init-volumes.py /usr/local/lib/tgd-init-volumes.py
RUN chmod 755 /entrypoint.sh
ARG TELEGRAM_BOT_API_REV=e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1
LABEL org.opencontainers.image.source="https://github.com/tdlib/telegram-bot-api" org.opencontainers.image.revision=$TELEGRAM_BOT_API_REV
WORKDIR /var/lib/telegram-bot-api
EXPOSE 8081
ENTRYPOINT ["/entrypoint.sh"]
