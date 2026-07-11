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

# Optional TensorTrade RL trader (mode == "rl"). OFF by default so the base image
# stays lean; enable with `docker build --build-arg INSTALL_RL=1 ...`.
# tensortrade's legacy setup.py needs --no-build-isolation (see requirements-rl.txt).
ARG INSTALL_RL=0
COPY requirements-rl.txt ./
RUN if [ "$INSTALL_RL" = "1" ]; then \
        pip install "setuptools<66" wheel && \
        pip install --no-build-isolation -r requirements-rl.txt ; \
    fi

# App code (brain/, data/ excluded via .dockerignore; tests/ shipped for the safety eval suite)
COPY trader/ ./trader/
COPY dashboard/ ./dashboard/
COPY tests/ ./tests/
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh && mkdir -p data

EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
