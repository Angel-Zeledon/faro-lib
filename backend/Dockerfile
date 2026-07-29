# Faro backend image — one build serves BOTH deployment roles:
#   API:    uvicorn backend.main:app       (WORKER_ENABLED/SCHEDULER_ENABLED=false)
#   Worker: python -m backend.workers      (training + cron loops)
#
# Build context is the REPO ROOT (the image needs ForecastingCore too):
#   docker build -f backend/Dockerfile .
#
# One shared image on purpose: the API imports forecasting_core widely (data
# profiler, optimizer, scenarios), so a slim API-only image would need a
# hand-curated dependency subset that drifts. Disk is cheap; drift is not.

FROM python:3.12-slim

# libgomp1: OpenMP runtime the LightGBM/XGBoost wheels link against.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Heavy, rarely-changing scientific deps first — this layer survives ordinary
# code changes. Versions track ForecastingCore/setup.py install_requires.
RUN pip install --no-cache-dir \
    "pandas>=1.5" "numpy>=1.23" "scikit-learn>=1.1" "lightgbm>=3.3" \
    "xgboost>=1.7" "prophet>=1.1" "statsmodels>=0.13" "scipy>=1.9" \
    "holidays>=0.20" "tensorflow-cpu>=2.16"

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# --no-deps: its dependencies ARE the pinned layer above.
COPY ForecastingCore /app/ForecastingCore
RUN pip install --no-cache-dir --no-deps /app/ForecastingCore

COPY backend /app/backend

# Non-root; /app/storage is the datasets/artifacts volume mount point shared
# with the worker container.
RUN useradd --create-home faro \
    && mkdir -p /app/storage \
    && chown -R faro:faro /app
USER faro

ENV PYTHONUNBUFFERED=1 \
    STORAGE_PATH=/app/storage

EXPOSE 8010

# Single process by default: with the in-process worker enabled (all-in-one
# mode), multiple uvicorn workers would run the cron loops once EACH.
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8010"]
