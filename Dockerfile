# SafeDrop — agentic forced-landing decision support.
#
# One image serves every entry point: the operations console, the benchmark,
# the variance study and the test suite. Which one runs is decided by the
# command, not by the build, so a judge reproduces the result with the same
# image the console runs on.
#
#   docker build -t safedrop .
#   docker run --rm -p 8000:8000 --env-file .env safedrop
#
# See REPRODUCE.md for the full set of commands.

FROM python:3.11-slim AS base

# Fail fast, no .pyc, unbuffered so `docker logs` shows progress live.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg

WORKDIR /app

# Dependencies first, so edits to the source do not invalidate this layer.
# Shapely, NumPy and pandas all ship manylinux wheels, so no build toolchain
# and no system GEOS are required.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Source and scenario data. The geometry cache under data/geo_cache/ is
# committed, so the offline benchmark reaches no third-party service except
# the model — it cannot break because Nominatim is down.
COPY app/ ./app/
COPY baseline/ ./baseline/
COPY evaluation/ ./evaluation/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY data/ ./data/
COPY config/ ./config/
COPY README.md REPRODUCE.md CHANGELOG.md pyproject.toml ./

# Outputs are written here and are usually bind-mounted back to the host.
RUN mkdir -p results trajectories/readable \
 && adduser --disabled-password --gecos "" --uid 10001 safedrop \
 && chown -R safedrop:safedrop /app
USER safedrop

EXPOSE 8000

# Liveness: the console answers, which also proves the scenario data loaded.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/scenarios',timeout=4)" || exit 1

# Default: the operations console. Override for anything else.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
