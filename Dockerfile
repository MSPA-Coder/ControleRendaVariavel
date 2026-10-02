# syntax=docker/dockerfile:1.7
# Base fixada por DIGEST do indice multi-arquitetura, e nao pela tag.
#
# `python:3.14-slim` e alvo movel: a tag e reapontada a cada republicacao, e
# como o `deploy.sh` reconstroi no VPS, a imagem servida podia nascer de uma
# base diferente da que a CI varreu. Mesmo raciocinio que ja fixa as actions
# por SHA e o Trivy por digest.
#
# E digest de INDICE, nao de manifesto: continua valendo para amd64 e arm64.
FROM python:3.14-slim@sha256:0097bb60d0c7a2c6af5a56e747eabc2016218f837f76daa70b9526fb883bc499 AS base
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

# A base está fixada por digest. Não fazemos `apt-get upgrade` contra o índice
# corrente: isso tornaria dois builds do mesmo commit diferentes. A varredura
# Trivy da imagem e a atualização periódica do digest são o caminho de segurança
# da base; pacotes adicionais são instalados com versão explícita abaixo.
RUN python -m pip install --no-cache-dir "pip==26.2.1" "setuptools==80.9.0" \
    && addgroup --system app \
    && adduser --system --ingroup app app

# OpenSSL: a base ainda traz 3.5.7-1~deb13u2, que o Trivy reprova (6 HIGH desde
# 30/09/2026: CVE-2026-75804, DoS por controle de fluxo QUIC, e CVE-2026-84782,
# vazamento por retransmissao DTLS; este app nao usa nenhum dos dois, o TLS
# termina no nginx). O Debian ja publicou a deb13u3 e nenhum digest de
# `python:3.14-slim` a traz ainda. Versao explicita, como o `git=` abaixo.
# REMOVER este bloco quando o digest da base trouxer a correcao: a CI acusa se
# a versao fixada sumir do indice do Debian.
RUN apt-get update \
    && apt-get install -y --no-install-recommends "openssl=3.5.7-1~deb13u3" "libssl3t64=3.5.7-1~deb13u3" \
    && rm -rf /var/lib/apt/lists/*

# libpcre2: a base traz 10.46-1~deb13u2, que o Trivy reprova (CVE-2026-103111,
# HIGH, escrita fora dos limites por expressao regular forjada). O Debian ja
# publicou a deb13u3 e o digest atual de `python:3.14-slim` ainda nao a traz.
# Mesmo tratamento do OpenSSL acima: versao explicita, e REMOVER este bloco
# quando o digest da base trouxer a correcao.
RUN apt-get update \
    && apt-get install -y --no-install-recommends "libpcre2-8-0=10.46-1~deb13u3" \
    && rm -rf /var/lib/apt/lists/*

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
    && apt-get install -y --no-install-recommends "git=1:2.47.3-0+deb13u1" \
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
    && apt-get install -y --no-install-recommends "git=1:2.47.3-0+deb13u1" \
    && rm -rf /var/lib/apt/lists/*
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"
COPY --chown=app:app pyproject.toml uv.lock README.md ./
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app tests ./tests
# A suite confere propriedades da implantacao (fuso, por exemplo) no proprio
# arquivo que o VPS usa.
COPY --chown=app:app compose.yaml ./
# `--extra dev` acrescenta as ferramentas de teste ao MESMO conjunto que o
# runtime instala: a suite tem de medir o que a imagem servida usa, e o lock
# garante que sejam as mesmas versoes.
RUN uv sync --locked --no-editable --extra dev
USER app
ENV RUFF_CACHE_DIR=/tmp/ruff-cache \
    PYTEST_ADDOPTS="-o cache_dir=/tmp/pytest-cache"
CMD ["sh", "-c", "ruff check . && pytest"]
