# syntax=docker/dockerfile:1
# One codebase, two images (build from the repository root):
#   docker build --target app      -t ragnar-app:0.2.0 -f example/docker/Dockerfile .        (ragnar mcp | migrate | index | sync | smoke | load)
#   docker build --target embedder -t ragnar-embedder:0.2.0 -f example/docker/Dockerfile .   (ragnar embedder, with the model baked in)
FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project --extra events
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --extra events

FROM build AS build-embed
RUN uv sync --frozen --no-dev --no-editable --extra embed --extra events

FROM python:3.12-slim AS app
ARG GITLEAKS_VERSION=8.30.1
ARG TARGETARCH
# gitleaks names its builds x64 and arm64; Docker says amd64 and arm64
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && arch=$([ "${TARGETARCH:-amd64}" = amd64 ] && echo x64 || echo "$TARGETARCH") \
 && curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${arch}.tar.gz" \
    | tar -xz -C /usr/local/bin gitleaks \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home ragnar \
 && mkdir /cache && chown 10001 /cache \
 && git config --system --add safe.directory '*'
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
CMD ["ragnar", "mcp"]

FROM python:3.12-slim AS embedder
ARG EMBED_MODEL=jinaai/jina-embeddings-v2-base-code
COPY --from=build-embed /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/models RAGNAR__EMBEDDING__MODEL=${EMBED_MODEL} HF_HUB_OFFLINE=0
RUN ragnar embed-preload && useradd --uid 10001 ragnar && chmod -R a+rX /models
ENV HF_HUB_OFFLINE=1
USER 10001
EXPOSE 8000
CMD ["ragnar", "embedder"]
