# syntax=docker/dockerfile:1.7
# Build from the repository root:
#   docker build -f deploy/linux/Dockerfile --target app .
#   docker build -f deploy/linux/Dockerfile --target research .
# deploy/linux/Dockerfile.dockerignore is an allowlist: only source, configs and
# the git-tracked redacted receipts listed there enter the build context. No
# private database, raw news, quotes, backups, tokens, .env or model weights.

FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39 AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC \
    HOME=/tmp

COPY apps/quant_portal/requirements.lock /tmp/req/requirements.lock
COPY deploy/linux/requirements-worker.txt /tmp/req/requirements-worker.txt
RUN python -m pip install --no-cache-dir -r /tmp/req/requirements.lock -r /tmp/req/requirements-worker.txt

RUN groupadd --system --gid 10001 quant \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin quant

WORKDIR /app
# Source stays root-owned and read-only for the service user.
COPY apps/quant_portal /app/apps/quant_portal
COPY scripts /app/scripts
COPY configs /app/configs
COPY results /app/results
COPY deploy/linux/entrypoint.py /app/deploy/linux/entrypoint.py

# Mount points for persistent named volumes. Docker copies this ownership into
# a new empty volume, so the non-root service user can write its private state.
RUN mkdir -p /app/apps/quant_portal/var /app/artifacts \
        /app/artifacts/news_sentiment_factor_v41_20261117/model \
    && chown -R 10001:10001 /app/apps/quant_portal/var /app/artifacts \
    && chown -R 10001:10001 /app/results/news_sentiment_factor_v42_20261117 \
        /app/results/news_sentiment_factor_v45_20261117 \
    && cd /app/apps/quant_portal \
    && SECRET_KEY=build-only-placeholder-not-for-runtime-012345678901234567890123456789 \
       ALLOWED_HOSTS=build.invalid DJANGO_TESTING=1 DEBUG=0 \
       python manage.py collectstatic --noinput >/dev/null \
    && find /app -name '__pycache__' -prune -exec rm -rf {} +

USER 10001:10001
ENTRYPOINT ["python", "/app/deploy/linux/entrypoint.py"]
CMD ["web"]

FROM app AS research
USER root
COPY deploy/linux/requirements-research.txt /tmp/req/requirements-research.txt
RUN python -m pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r /tmp/req/requirements-research.txt
USER 10001:10001
CMD ["research"]
