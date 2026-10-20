FROM golang:1.24.10-bookworm@sha256:aaa1dedbfef846dbd62fa803cecd7486b0e98a32cb6f68656eee20acac5dd53a AS build
ARG SING_BOX_REV=bc35aca01704497c179da1a03e45ad8e32f1a51b
RUN git init /src && cd /src && git remote add origin https://github.com/SagerNet/sing-box.git && git fetch --depth 1 origin "$SING_BOX_REV" && git checkout --detach FETCH_HEAD
WORKDIR /src
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w -X github.com/sagernet/sing-box/constant.Version=1.11.15' -o /out/sing-box ./cmd/sing-box

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
RUN apt-get update && apt-get install -y --no-install-recommends iptables iproute2 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/sing-box /usr/local/bin/sing-box
COPY --from=build /src/LICENSE /usr/share/licenses/sing-box/LICENSE
COPY deploy/network/supervisor.py /opt/tgd/supervisor.py
ARG SING_BOX_REV=bc35aca01704497c179da1a03e45ad8e32f1a51b
LABEL org.opencontainers.image.source="https://github.com/SagerNet/sing-box" org.opencontainers.image.revision=$SING_BOX_REV
ENTRYPOINT ["python", "/opt/tgd/supervisor.py"]
