FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/setuora

COPY requirements-runtime.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements-runtime.lock

RUN addgroup --system setuora \
    && adduser --system --ingroup setuora --home /srv/setuora setuora \
    && mkdir -p /srv/setuora/data \
    && chown -R setuora:setuora /srv/setuora

COPY --chown=setuora:setuora app ./app

USER setuora

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
