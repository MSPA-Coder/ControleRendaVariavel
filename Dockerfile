# syntax=docker/dockerfile:1.7
# Base fixada por DIGEST do indice multi-arquitetura, e nao pela tag.
#
# `python:3.14-slim` e alvo movel: a tag e reapontada a cada republicacao, e
# como o `deploy.sh` reconstroi no VPS, a imagem servida podia nascer de uma
# base diferente da que a CI varreu. Mesmo raciocinio que ja fixa as actions
# por SHA e o Trivy por digest.
#
# E digest de INDICE, nao de manifesto: continua valendo para amd64 e arm64.
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS base
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

# builder: resolve as dependencias a partir do `uv.lock`, num venv isolado.
#
# POR QUE `uv` E NAO `pip install .`: o `pip` resolvia as faixas do
# `pyproject.toml` no instante do build, entao dois builds do MESMO commit
# podiam produzir imagens diferentes -- e o `deploy.sh` reconstroi no VPS, de
# modo que a imagem servida nunca foi exatamente a que a CI testou. O
# `uv.lock` fixa versao e hash SHA-256 de cada dependencia.
#
# O FLAG E `--locked`, E A DIFERENCA IMPORTA. `--frozen` apenas usa o lock sem
# olhar o `pyproject.toml`: com o lock desatualizado ele sai com SUCESSO e
# instala as versoes antigas, em silencio. `--locked` confere e REPROVA quando
# alguem edita a declaracao e esquece de rodar `uv lock`.
#
# POR QUE ISSO IMPORTA MAIS AQUI: `sharedauth` vem de repositorio Git, e o lock
# o prende ao COMMIT, nao a tag.
#
# O venv em `/opt/venv` e novo neste projeto -- antes a instalacao ia para o
# Python do sistema. Alem de ser o que o `uv` espera, aproxima este Dockerfile
# do MegaSena, que ja era assim.
#
# `git` continua necessario: e como o `sharedauth` e obtido. O que saiu foi o
# token, porque o repositorio e publico.
FROM base AS builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
# Versao fixa: o instalador que garante reprodutibilidade nao pode ser ele
# proprio uma variavel. O binario e autocontido, e o estagio `quality` o copia
# daqui em vez de reinstala-lo.
RUN python -m pip install --no-cache-dir --upgrade "uv==0.12.10"
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN uv sync --locked --no-editable

FROM base AS runtime
ENV PATH="/opt/venv/bin:${PATH}"
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY migrations ./migrations
# `pip` e `setuptools` são ferramentas de build e não fazem parte do runtime.
# Removê-los reduz a superfície da imagem servida; a última linha faz o build
# falhar caso `pip` ainda permaneça no PATH.
#
# O `python -m pip check` que abria este bloco saiu com a adocao do `uv`: ele
# perguntava se as dependencias instaladas sao mutuamente compativeis, e o venv
# criado pelo `uv` nem tem `pip` para responder. A pergunta tambem deixou de
# fazer sentido -- o conjunto vem resolvido do lock, entao a coerencia e
# garantida na resolucao, nao conferida depois.
RUN set -eu; \
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
# O `uv` vem pronto do `builder`: e binario autocontido, e copia-lo custa menos
# que reinstalar um gerenciador de pacotes.
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"
COPY --chown=app:app pyproject.toml uv.lock README.md ./
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app tests ./tests
# `--extra dev` acrescenta as ferramentas de teste ao MESMO conjunto que o
# runtime instala: a suite tem de medir o que a imagem servida usa, e o lock
# garante que sejam as mesmas versoes.
RUN uv sync --locked --no-editable --extra dev
USER app
ENV RUFF_CACHE_DIR=/tmp/ruff-cache \
    PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest-cache"
CMD ["sh", "-c", "ruff check . && pytest"]
