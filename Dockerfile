# Backend container: FastAPI telemetry/API for the platform brain + (optional) daemons.
# The Next.js brain graph is deployed separately on Vercel and points at this
# service via NEXT_PUBLIC_TELEMETRY_BASE.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    RUN_DAEMONS=1

# System deps kept minimal; wheels cover numpy/pandas on slim.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# App code (brain/, data/, tests/ excluded via .dockerignore)
COPY trader/ ./trader/
COPY dashboard/ ./dashboard/
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh && mkdir -p data

EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
