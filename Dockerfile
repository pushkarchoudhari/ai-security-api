# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Build stage -- compilers and build tooling stay here and never ship.
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Dependencies are installed from the manifest alone so this layer caches
# independently of application source changes.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

# ---------------------------------------------------------------------------
# Runtime stage -- no build toolchain, no package manager state, non-root.
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

LABEL org.opencontainers.image.title="ai-security-api" \
      org.opencontainers.image.description="Secure AI enterprise API with layered LLM security controls" \
      org.opencontainers.image.source="https://github.com/pushkarchoudhari/ai-security-api" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    LLM_MODE=mock \
    AUDIT_LOG_PATH=/app/data/audit.log \
    TRUST_PROXY_HEADERS=true

# Unprivileged runtime user. A container escape from the app process should not
# land on root, and nothing in the image needs elevated privileges.
RUN groupadd --system --gid 10001 appuser \
 && useradd --system --uid 10001 --gid appuser --no-create-home appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY app ./app

# The audit log is the only path the process writes to.
RUN mkdir -p /app/data && chown -R appuser:appuser /app/data

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
