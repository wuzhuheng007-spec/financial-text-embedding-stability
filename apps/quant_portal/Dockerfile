FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir -r /app/requirements.lock

COPY . /app
RUN mkdir -p /app/staticfiles /app/var/media \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin portal \
    && chown -R portal:portal /app
USER portal

# The build-time value is intentionally unusable as a deployed secret.
RUN SECRET_KEY=build-only-placeholder-not-for-runtime-012345678901234567890123456789 \
    ALLOWED_HOSTS=build.invalid DJANGO_TESTING=1 DEBUG=0 \
    python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["gunicorn", "portal.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--threads", "2", "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-"]
