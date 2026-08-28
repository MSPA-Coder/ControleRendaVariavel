# syntax=docker/dockerfile:1.7
FROM python:3.14-slim AS base
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache
WORKDIR /app
RUN --mount=type=secret,id=local_ca,required=false \
    if [ -f /run/secrets/local_ca ]; then \
      cp /run/secrets/local_ca /usr/local/share/ca-certificates/local-root-ca.crt \
      && update-ca-certificates; \
    fi

# Correcoes de seguranca da base e das ferramentas de empacotamento.
#
# `apt-get upgrade` porque a `python:3.14-slim` publicada carrega pacotes do
# Debian com CVE ja corrigido a montante; sem isto a correcao so chega quando a
# imagem oficial for republicada. O `setuptools` que vem na base tambem fica
# para tras -- o 70.3.0 tinha CVE-2025-47273, travessia de caminho.
#
# A atualização inclui correções publicadas antes que a imagem base seja
# republicada.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --upgrade "pip>=26.1.2,<27" setuptools \
    && addgroup --system app \
    && adduser --system --ingroup app app

FROM base AS runtime
COPY pyproject.toml README.md ./
COPY app ./app
# `pyproject.toml` inclui `sharedauth` de um repositório Git privado
# (github.com/MSPA-Coder/SharedAuth) -- pip precisa de `git` no PATH e de
# credencial para HTTPS. O secret `github_token` (BuildKit, nunca vira camada
# da imagem) autentica só para este RUN; `git config --unset` no fim da mesma
# instrução remove o token do `.gitconfig` antes de commitar a camada.
RUN --mount=type=secret,id=github_token \
    apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global url."https://x-access-token:$(cat /run/secrets/github_token)@github.com/".insteadOf "https://github.com/" \
    && pip install --no-cache-dir . \
    && git config --global --unset url."https://x-access-token:$(cat /run/secrets/github_token)@github.com/".insteadOf
COPY migrations ./migrations
# `pip` e `setuptools` são ferramentas de build e não fazem parte do runtime.
# Removê-los reduz a superfície da imagem servida; a última linha faz o build
# falhar caso `pip` ainda permaneça no PATH.
RUN set -eu; \
    python -m pip check; \
    for raiz in /usr/local/lib/python*/site-packages /opt/venv/lib/python*/site-packages; do \
      [ -d "$raiz" ] || continue; \
      rm -rf "$raiz"/pip "$raiz"/pip-*.dist-info \
             "$raiz"/setuptools "$raiz"/setuptools-*.dist-info \
             "$raiz"/pkg_resources "$raiz"/_distutils_hack \
             "$raiz"/distutils-precedence.pth \
             "$raiz"/wheel "$raiz"/wheel-*.dist-info; \
    done; \
    rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.* \
          /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.*; \
    ! command -v pip

USER app
EXPOSE 5003
CMD ["gunicorn", "--bind", "0.0.0.0:5003", "--workers", "2", "--threads", "4", "--worker-class", "gthread", "--timeout", "60", "--no-control-socket", "app:create_app()"]

# quality: Ruff e a suite minima de seguranca. Nunca e a imagem servida --
# `compose.yaml` usa `runtime` para web e migrate.
FROM base AS quality
COPY --chown=app:app pyproject.toml README.md ./
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app tests ./tests
RUN --mount=type=secret,id=github_token \
    apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global url."https://x-access-token:$(cat /run/secrets/github_token)@github.com/".insteadOf "https://github.com/" \
    && pip install --no-cache-dir ".[dev]" \
    && git config --global --unset url."https://x-access-token:$(cat /run/secrets/github_token)@github.com/".insteadOf
USER app
ENV RUFF_CACHE_DIR=/tmp/ruff-cache \
    PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest-cache"
CMD ["sh", "-c", "ruff check . && pytest"]
