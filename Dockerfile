FROM node:24.11.1-bookworm-slim@sha256:48abc13a19400ca3985071e287bd405a1d99306770eb81d61202fb6b65cf0b57 AS web
WORKDIR /build
RUN npm install --global pnpm@11.23.0
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS python-build
RUN pip install --no-cache-dir uv==0.12.5
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/ ./

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
LABEL org.opencontainers.image.version="0.1.0"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/backend/.venv/bin:$PATH" TGD_STATIC_DIR=/app/frontend/dist
WORKDIR /app/backend
COPY --from=python-build /app/backend /app/backend
COPY --from=web /build/dist /app/frontend/dist
COPY deploy/app-entrypoint.py /app/entrypoint.py
USER 1000:1000
EXPOSE 8000
ENTRYPOINT ["python", "/app/entrypoint.py"]
