# Faro — Demand Forecasting Platform

Enterprise-grade demand and sales forecasting platform. Combines a Python forecasting engine with a REST API backend and a Next.js frontend.

## Repository Structure

```
faro-lib/
├── ForecastingCore/         # Python forecasting libraries
│   ├── forecasting_core/        # faro-core: forecasting engine
│   ├── forecastlib/             # faro-prep: data preprocessing
│   └── faro_prep_build/         # build config for faro-prep
├── backend/                 # FastAPI REST API
└── Frontend/                # Next.js web application
```

## Libraries on PyPI

| Package | Install | Description |
|---------|---------|-------------|
| `faro-core` | `pip install faro-core` | Multi-SKU forecasting engine |
| `faro-prep` | `pip install faro-prep` | Data preprocessing pipeline |

## Local Development

```bash
# Install Python libraries (editable)
pip install -e ForecastingCore/

# Backend
pip install -r backend/requirements.txt

# Frontend
cd Frontend && npm install && npm run dev
```
