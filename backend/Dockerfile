# BETTORY backend: the FastAPI app and (optionally) the scheduler in one container (ARCHITECTURE.md 5, option A).
# Build context: backend/. Secrets are never in the image: every setting comes from the host's environment
# variables (Render dashboard). See docs/DEPLOYMENT.md.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend

# dependencies first (cached layer); pinned to the versions the test suite ran with
COPY requirements.txt .
RUN pip install -r requirements.txt

# the source is used in place (not pip-installed): app/ finds config/ relative to itself
COPY app ./app
COPY config ./config
COPY backtests ./backtests
COPY deploy ./deploy
RUN chmod +x deploy/start.sh \
    && useradd --create-home --uid 10001 bettory \
    && mkdir -p data && chown -R bettory:bettory /app
USER bettory

ENV ENVIRONMENT=production PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"

CMD ["deploy/start.sh"]
