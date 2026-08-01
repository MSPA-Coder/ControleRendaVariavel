# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache
WORKDIR /app
RUN python -m pip install --no-cache-dir "pip==26.1.2" \
    && addgroup --system app \
    && adduser --system --ingroup app app \
    && chown -R app:app /app

FROM base AS runtime
COPY --chown=app:app pyproject.toml README.md ./
COPY --chown=app:app app ./app
RUN --mount=type=secret,id=host_ca,required=false \
    if [ -f /run/secrets/host_ca ]; then \
      cp /run/secrets/host_ca /usr/local/share/ca-certificates/host-ca.crt \
      && update-ca-certificates; \
    fi \
    && pip install --no-cache-dir .
COPY --chown=app:app migrations ./migrations
USER app
EXPOSE 5003
CMD ["gunicorn", "--bind", "0.0.0.0:5003", "--workers", "1", "--timeout", "30", "app:create_app()"]

FROM base AS development
COPY --chown=app:app pyproject.toml README.md ./
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app tests ./tests
RUN --mount=type=secret,id=host_ca,required=false \
    if [ -f /run/secrets/host_ca ]; then \
      cp /run/secrets/host_ca /usr/local/share/ca-certificates/host-ca.crt \
      && update-ca-certificates; \
    fi \
    && pip install --no-cache-dir ".[dev]"
USER app
CMD ["pytest"]
