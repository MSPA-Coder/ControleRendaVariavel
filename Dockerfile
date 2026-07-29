# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md ./
COPY app ./app
RUN --mount=type=secret,id=host_ca,required=false \
    if [ -f /run/secrets/host_ca ]; then \
      cp /run/secrets/host_ca /usr/local/share/ca-certificates/host-ca.crt \
      && update-ca-certificates; \
    fi \
    && pip wheel --wheel-dir /wheels .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN addgroup --system app && adduser --system --ingroup app app
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY app ./app
COPY migrations ./migrations
COPY pyproject.toml ./
USER app
EXPOSE 5003
CMD ["sh", "-c", "flask --app app:create_app db upgrade && exec gunicorn --bind 0.0.0.0:5003 --workers 2 --timeout 30 'app:create_app()'"]
