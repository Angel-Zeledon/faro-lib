FROM python:3.11-slim

WORKDIR /app

# Install system deps needed by LightGBM, XGBoost, Prophet, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install ForecastingCore first (rarely changes — cached layer)
COPY ForecastingCore/ ./ForecastingCore/
RUN pip install --no-cache-dir -e ./ForecastingCore

# Install backend requirements
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# Copy backend source
COPY backend/ ./backend/

# Storage dir (mounted as volume in compose)
RUN mkdir -p /app/storage/datasets /app/storage/artifacts

EXPOSE 8001

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
