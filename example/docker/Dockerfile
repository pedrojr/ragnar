# syntax=docker/dockerfile:1
# Uma base de código, duas imagens:
#   docker build --target app      -t ragnar-app:0.1.0 .        (ragnar mcp | migrate | index | smoke | load)
#   docker build --target embedder -t ragnar-embedder:0.1.0 .   (ragnar embedder, modelo embutido)
FROM ghcr.io/astral-sh/uv:0.11.28 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM build AS build-embed
RUN uv sync --frozen --no-dev --no-editable --extra embed

FROM python:3.12-slim AS app
ARG GITLEAKS_VERSION=8.30.1
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl \
 && curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    | tar -xz -C /usr/local/bin gitleaks \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --create-home ragnar \
 && git config --system --add safe.directory '*'
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
CMD ["ragnar", "mcp"]

FROM python:3.12-slim AS embedder
COPY --from=build-embed /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/models EMBED_MODEL=jinaai/jina-embeddings-v2-base-code HF_HUB_OFFLINE=0
RUN ragnar embed-preload && useradd --uid 10001 ragnar && chmod -R a+rX /models
ENV HF_HUB_OFFLINE=1
USER 10001
EXPOSE 8000
CMD ["ragnar", "embedder"]
