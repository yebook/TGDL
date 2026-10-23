# Default to the source-built stage; publication may supply an immutable native image.
ARG TGD_NATIVE_IMAGE=native
FROM scratch AS native-inputs
COPY .dockerignore /.dockerignore
COPY deploy/bot-api/Dockerfile deploy/bot-api/native_identity.py deploy/bot-api/native-proxy.patch deploy/bot-api/native-progress.patch deploy/bot-api/ProgressCache.h deploy/bot-api/native-cache-cleanup.patch deploy/bot-api/CacheCleanup.h /deploy/bot-api/

FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587 AS build
ARG TARGETARCH
RUN test "$TARGETARCH" = amd64 || { echo 'TGDL native builds currently support linux/amd64 only' >&2; exit 1; }
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends ca-certificates && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends git build-essential ccache cmake gperf libssl-dev zlib1g-dev && rm -rf /var/lib/apt/lists/*
# Official source commit, queried 2026-10-20. Submodule commits are pinned by it.
ARG TELEGRAM_BOT_API_REV=e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1
ARG TELEGRAM_TDLIB_REV=bc9c263e2bfee06aaab41e82db51a103376030bc
RUN git init /src && cd /src && git remote add origin https://github.com/tdlib/telegram-bot-api.git && git fetch --depth 1 origin "$TELEGRAM_BOT_API_REV" && git checkout --detach FETCH_HEAD && git submodule update --init --recursive --depth 1 && \
    test "$(git -C /src rev-parse HEAD)" = "$TELEGRAM_BOT_API_REV" && test "$(git -C /src/td rev-parse HEAD)" = "$TELEGRAM_TDLIB_REV"
COPY deploy/bot-api/native-proxy.patch /native-proxy.patch
ARG TGD_BUILD_JOBS=2
# Scope compiler objects to the target/source/toolchain; ccache also hashes compiler contents.
ENV CCACHE_DIR=/root/.cache/ccache CCACHE_COMPILERCHECK=content CCACHE_MAXSIZE=2G
RUN --mount=type=cache,id=tgd-ccache-bookworm-gcc12-${TARGETARCH}-${TELEGRAM_BOT_API_REV},target=/root/.cache/ccache,sharing=locked \
    case "$TGD_BUILD_JOBS" in ''|*[!0-9]*) echo 'TGD_BUILD_JOBS must be an integer from 1 to 64' >&2; exit 1;; esac && \
    test "$TGD_BUILD_JOBS" -ge 1 && test "$TGD_BUILD_JOBS" -le 64 && \
    cd /src && git apply --check /native-proxy.patch && git apply /native-proxy.patch && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/out -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache && cmake --build build --parallel "$TGD_BUILD_JOBS" && cmake --install build
COPY deploy/bot-api/native-progress.patch /native-progress.patch
COPY deploy/bot-api/ProgressCache.h /src/telegram-bot-api/ProgressCache.h
RUN --mount=type=cache,id=tgd-ccache-bookworm-gcc12-${TARGETARCH}-${TELEGRAM_BOT_API_REV},target=/root/.cache/ccache,sharing=locked \
    cd /src && git apply --check /native-progress.patch && git apply /native-progress.patch && cmake --build build --parallel "$TGD_BUILD_JOBS" && cmake --install build
COPY deploy/bot-api/native-cache-cleanup.patch /native-cache-cleanup.patch
COPY deploy/bot-api/CacheCleanup.h /src/telegram-bot-api/CacheCleanup.h
RUN --mount=type=cache,id=tgd-ccache-bookworm-gcc12-${TARGETARCH}-${TELEGRAM_BOT_API_REV},target=/root/.cache/ccache,sharing=locked \
    cd /src && git apply --check /native-cache-cleanup.patch && git apply /native-cache-cleanup.patch && cmake --build build --parallel "$TGD_BUILD_JOBS" && cmake --install build
RUN mkdir -p /out/licenses/tdlib /out/share && cp /src/LICENSE* /out/licenses/ && cp /src/td/LICENSE* /out/licenses/tdlib/ && \
    printf '{"bot_api_revision":"%s","tdlib_revision":"%s"}\n' "$(git -C /src rev-parse HEAD)" "$(git -C /src/td rev-parse HEAD)" > /out/share/tgdl-source.json

FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587 AS native
RUN apt-get -o Acquire::Retries=3 update && apt-get -o Acquire::Retries=3 install -y --no-install-recommends ca-certificates libssl3 zlib1g libstdc++6 python3 python3-cryptography && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/ /usr/local/
ARG TELEGRAM_BOT_API_REV=e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1
LABEL org.opencontainers.image.source="https://github.com/tdlib/telegram-bot-api" org.opencontainers.image.revision=$TELEGRAM_BOT_API_REV
RUN --mount=type=bind,from=native-inputs,target=/tgd-source \
    python3 /tgd-source/deploy/bot-api/native_identity.py write --root /tgd-source --output /usr/local/share/tgdl-native.json --source /usr/local/share/tgdl-source.json
# TGD_NATIVE_INPUTS_END

FROM ${TGD_NATIVE_IMAGE} AS runtime
RUN --mount=type=bind,from=native-inputs,target=/tgd-source \
    python3 /tgd-source/deploy/bot-api/native_identity.py verify --root /tgd-source --manifest /usr/local/share/tgdl-native.json
LABEL org.opencontainers.image.version="0.1.0"
COPY --chmod=0755 deploy/bot-api/entrypoint.sh /entrypoint.sh
COPY --chmod=0644 deploy/bot-api/supervisor.py /usr/local/lib/tgd-api-supervisor.py
COPY --chmod=0644 deploy/bot-api/init-volumes.py /usr/local/lib/tgd-init-volumes.py
WORKDIR /var/lib/telegram-bot-api
EXPOSE 8081
ENTRYPOINT ["/entrypoint.sh"]
