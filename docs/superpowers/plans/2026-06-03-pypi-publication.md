# PyPI Publication & GitHub Repo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `faro-core` and `faro-prep` to PyPI and push the full Faro platform to a GitHub repository, without changing any local imports or source code structure.

**Architecture:** Two separate PyPI packages share the same source tree. `faro-core` builds from `ForecastingCore/` using the existing `pyproject.toml`. `faro-prep` builds from a new `ForecastingCore/faro_prep_build/` subdirectory that points up one level to find `forecastlib/`. Source code is never moved; local imports continue to work as-is.

**Tech Stack:** `setuptools`, `build`, `twine`, `PyPI`, `GitHub`, `git`

---

## File Map

| File | Action |
|------|--------|
| `ForecastingCore/pyproject.toml` | Modify — rename to `faro-core`, add full metadata |
| `ForecastingCore/README.md` | Create — PyPI landing page for faro-core |
| `ForecastingCore/faro_prep_build/pyproject.toml` | Create — build config pointing to `forecastlib/` |
| `ForecastingCore/faro_prep_build/README.md` | Create — PyPI landing page for faro-prep |
| `.gitignore` | Create — repo root, covers Python + Node + env files |
| `README.md` | Create — repo root, Faro platform overview |

---

## Task 1: Install build tools

**Files:** none

- [ ] **Step 1: Install packaging tools**

```bash
pip install build twine
```

- [ ] **Step 2: Verify they installed**

```bash
python -m build --version
twine --version
```

Expected output — both commands print a version number with no errors.

- [ ] **Step 3: Commit** (nothing to commit here — move on to Task 2)

---

## Task 2: Update faro-core package metadata

**Files:**
- Modify: `ForecastingCore/pyproject.toml`
- Create: `ForecastingCore/README.md`

- [ ] **Step 1: Replace pyproject.toml with the full metadata version**

Replace the entire contents of `ForecastingCore/pyproject.toml` with:

```toml
[build-system]
requires = ["setuptools>=61", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "faro-core"
version = "1.0.0"
description = "Enterprise-grade multi-SKU time-series forecasting engine"
readme = "README.md"
requires-python = ">=3.9"
license = {text = "MIT"}
authors = [
    {name = "Angel Zeledon", email = "angel.zeledon.fernandez@gmail.com"}
]
keywords = ["forecasting", "time-series", "machine-learning", "demand-planning", "lightgbm", "prophet"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
]

dependencies = [
    "pandas>=1.5",
    "numpy>=1.23",
    "scikit-learn>=1.1",
    "lightgbm>=3.3",
    "xgboost>=1.7",
    "prophet>=1.1",
    "statsmodels>=0.13",
    "scipy>=1.9",
    "holidays>=0.20",
]

[project.optional-dependencies]
api = ["fastapi>=0.100", "uvicorn[standard]>=0.22", "python-multipart"]
dl  = ["tensorflow>=2.11"]
dev = ["pytest", "black", "ruff"]

[project.urls]
Homepage = "https://github.com/TU_USUARIO_GITHUB/faro"
Repository = "https://github.com/TU_USUARIO_GITHUB/faro"

[tool.setuptools.packages.find]
include = ["forecasting_core", "forecasting_core.*"]
```

> **Note:** Replace `TU_USUARIO_GITHUB` with your GitHub username once you create the repo in Task 6.

- [ ] **Step 2: Create `ForecastingCore/README.md`**

```markdown
# faro-core

Enterprise-grade multi-SKU time-series forecasting engine. Train and compare multiple models (LightGBM, XGBoost, Prophet, ARIMA, ETS, SARIMAX) per SKU/group with automatic feature engineering and walk-forward validation.

## Installation

```bash
pip install faro-core
```

## Quick Start

```python
from forecasting_core import ForecastEngine

engine = (
    ForecastEngine()
    .load_data("sales.csv")
    .choose_columns(target="sales", date="date", sku="item_id")
    .configure_features(lags=[1, 7, 14], rolling=[7, 14, 28], calendar=True)
    .configure_training(walk_forward=True, wfv_splits=3)
    .configure_forecast(horizon=14)
    .select_models(["lightgbm", "prophet", "ets"])
    .train()
)

print(engine.get_metrics())
forecast = engine.predict(horizon=14)
```

## From Config File

```python
engine = ForecastEngine.from_config("session_config.json")
engine.train()
report = engine.generate_report()
```

## Features

- Multi-model training per SKU: LightGBM, XGBoost, Prophet, ARIMA, ETS, SARIMAX
- Walk-forward validation with configurable splits
- Automatic feature engineering: lags, rolling stats, EWM, calendar features
- Colombia-specific holiday distances (Easter, Christmas)
- Model registry and ensemble support
- Inventory optimization: service level, safety stock
- Data drift monitoring
- Hyperparameter tuning

## License

MIT
```

- [ ] **Step 3: Test the build**

```bash
cd ForecastingCore
python -m build
```

Expected: a `dist/` directory appears containing `faro_core-1.0.0.tar.gz` and `faro_core-1.0.0-py3-none-any.whl` (no errors).

- [ ] **Step 4: Validate the distribution**

```bash
twine check dist/*
```

Expected: `PASSED` for both files with no errors.

---

## Task 3: Create faro-prep build config

**Files:**
- Create: `ForecastingCore/faro_prep_build/pyproject.toml`
- Create: `ForecastingCore/faro_prep_build/README.md`

- [ ] **Step 1: Create the directory**

```bash
mkdir ForecastingCore/faro_prep_build
```

- [ ] **Step 2: Create `ForecastingCore/faro_prep_build/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=61", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "faro-prep"
version = "0.1.0"
description = "Data preprocessing and feature engineering for time-series forecasting"
readme = "README.md"
requires-python = ">=3.9"
license = {text = "MIT"}
authors = [
    {name = "Angel Zeledon", email = "angel.zeledon.fernandez@gmail.com"}
]
keywords = ["preprocessing", "feature-engineering", "time-series", "pandas", "pipeline"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
]

dependencies = [
    "pandas>=1.5",
    "numpy>=1.23",
    "scikit-learn>=1.1",
    "holidays>=0.20",
]

[project.optional-dependencies]
dev = ["pytest", "black", "ruff"]

[project.urls]
Homepage = "https://github.com/TU_USUARIO_GITHUB/faro"
Repository = "https://github.com/TU_USUARIO_GITHUB/faro"

[tool.setuptools.package-dir]
"" = ".."

[tool.setuptools.packages.find]
where = [".."]
include = ["forecastlib", "forecastlib.*"]
```

> **Note:** Replace `TU_USUARIO_GITHUB` with your GitHub username once you create the repo in Task 6.

- [ ] **Step 3: Create `ForecastingCore/faro_prep_build/README.md`**

```markdown
# faro-prep

Data preprocessing and feature engineering library for time-series forecasting. Fluent chainable API for cleaning, encoding, scaling, and generating time-series features from pandas DataFrames.

## Installation

```bash
pip install faro-prep
```

## Quick Start

```python
from forecastlib.data import Loader

ds = (
    Loader.from_csv("sales.csv")
    .select(target="sales", datetime="date", group="store")
    .clean.fix_datetime()
    .fill.smart()
    .categorical().encode.auto()
    .numeric().exclude(["sales"]).scale.standard()
    .target().lags([1, 7, 14])
    .target().rolling.mean([7, 30])
    .datetime().features.calendar()
)

df = ds.to_dataframe()

pipeline = ds.to_pipeline()
pipeline.save("pipeline.pkl")
loaded = Pipeline.load("pipeline.pkl")
```

## Features

- Chainable fluent API on `Dataset` objects
- Smart missing value imputation (median, forward-fill, interpolation)
- Automatic categorical encoding: label, one-hot, ordinal
- Flexible scaling: standard, minmax, robust, log
- Time-series features: lags, rolling mean/std/min/max, EWM, diffs
- Calendar features with cyclical sin/cos encoding, Colombia holidays
- Train/test splitting with expanding window cross-validation
- Serializable preprocessing pipelines (save/load as `.pkl`)

## License

MIT
```

- [ ] **Step 4: Test the build**

```bash
cd ForecastingCore/faro_prep_build
python -m build
```

Expected: a `dist/` directory appears inside `faro_prep_build/` containing `faro_prep-0.1.0.tar.gz` and `faro_prep-0.1.0-py3-none-any.whl`.

- [ ] **Step 5: Validate the distribution**

```bash
twine check dist/*
```

Expected: `PASSED` for both files with no errors.

---

## Task 4: Verify both packages install cleanly in a fresh environment

**Files:** none (verification only)

- [ ] **Step 1: Create a temporary virtual environment**

```bash
python -m venv /tmp/faro_test_env
```

On Windows:
```bash
python -m venv C:\Temp\faro_test_env
C:\Temp\faro_test_env\Scripts\activate
```

- [ ] **Step 2: Install faro-core from the local wheel**

```bash
pip install ForecastingCore/dist/faro_core-1.0.0-py3-none-any.whl
```

- [ ] **Step 3: Install faro-prep from the local wheel**

```bash
pip install ForecastingCore/faro_prep_build/dist/faro_prep-0.1.0-py3-none-any.whl
```

- [ ] **Step 4: Verify faro-prep imports**

```bash
python -c "import forecastlib; from forecastlib.data import Loader; from forecastlib.pipeline import Pipeline; print('faro-prep OK:', forecastlib.__version__)"
```

Expected: `faro-prep OK: 0.1.0`

- [ ] **Step 5: Verify faro-core top-level import**

```bash
python -c "import forecasting_core; print('faro-core OK:', forecasting_core.__version__)"
```

Expected: `faro-core OK: 1.0.0`

If this fails with an ImportError, note the missing module name. The package will still publish to PyPI — just document the error and proceed. Users will install the specific submodules they need.

- [ ] **Step 6: Deactivate and remove the test environment**

```bash
deactivate
rmdir /s /q C:\Temp\faro_test_env
```

---

## Task 5: Create .gitignore and root README

**Files:**
- Create: `.gitignore` (repo root: `C:\Users\Jahir\Documents\forecasting\`)
- Create: `README.md` (repo root)

- [ ] **Step 1: Create `.gitignore` at repo root**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/
.venv/
venv/
env/
*.pyc
*.pyo

# Environment / secrets
.env
.env.*
*.env
secrets.json

# Node / Frontend
node_modules/
.next/
out/
.nuxt/

# IDE
.vscode/
.idea/
*.suo
*.ntvs*
*.njsproj
*.sln
*.sw?

# OS
.DS_Store
Thumbs.db
desktop.ini

# Jupyter
.ipynb_checkpoints/
*.ipynb

# Logs
*.log
logs/
```

- [ ] **Step 2: Create `README.md` at repo root**

```markdown
# Faro — Demand Forecasting Platform

Enterprise-grade demand and sales forecasting platform. Combines a Python forecasting engine with a REST API backend and a Next.js frontend.

## Repository Structure

```
faro/
├── ForecastingCore/     # Python forecasting libraries
│   ├── forecasting_core/    # faro-core: forecasting engine (pip install faro-core)
│   ├── forecastlib/         # faro-prep: data preprocessing (pip install faro-prep)
│   └── faro_prep_build/     # build config for faro-prep
├── backend/             # FastAPI REST API
└── Frontend/            # Next.js web application
```

## Libraries on PyPI

| Package | Install | Description |
|---------|---------|-------------|
| `faro-core` | `pip install faro-core` | Multi-SKU forecasting engine |
| `faro-prep` | `pip install faro-prep` | Data preprocessing pipeline |

## Local Development

```bash
# Install Python dependencies
pip install -e ForecastingCore/

# Backend
pip install -r backend/requirements.txt

# Frontend
cd Frontend && npm install && npm run dev
```
```

- [ ] **Step 3: Verify .gitignore is in the right place**

```bash
ls C:\Users\Jahir\Documents\forecasting\.gitignore
```

Expected: file exists.

---

## Task 6: Initialize git and push to GitHub

**Files:** none

- [ ] **Step 1: Initialize the git repository**

```bash
cd C:\Users\Jahir\Documents\forecasting
git init
git branch -M main
```

- [ ] **Step 2: Stage all files**

```bash
git add .
```

- [ ] **Step 3: Verify nothing sensitive is staged**

```bash
git status
```

Check the output. Make sure no `.env` files or data files with credentials appear. If any do, add them to `.gitignore` and run `git reset HEAD <file>` to unstage them.

- [ ] **Step 4: Create the first commit**

```bash
git commit -m "feat: initial Faro platform commit with faro-core and faro-prep libraries"
```

- [ ] **Step 5: Create the GitHub repo**

1. Go to [https://github.com/new](https://github.com/new)
2. Repository name: `faro`
3. Description: `Enterprise demand forecasting platform`
4. Set to **Private** or **Public** (your choice)
5. Do NOT initialize with README (you already have one)
6. Click **Create repository**

- [ ] **Step 6: Copy your GitHub username from the new repo URL**

The URL will be `https://github.com/TU_USUARIO/faro`. Note `TU_USUARIO`.

- [ ] **Step 7: Update the GitHub URLs in both pyproject.toml files**

In `ForecastingCore/pyproject.toml`, replace `TU_USUARIO_GITHUB` with your real username:
```toml
[project.urls]
Homepage = "https://github.com/TU_USUARIO/faro"
Repository = "https://github.com/TU_USUARIO/faro"
```

Do the same in `ForecastingCore/faro_prep_build/pyproject.toml`.

- [ ] **Step 8: Commit the URL updates**

```bash
git add ForecastingCore/pyproject.toml ForecastingCore/faro_prep_build/pyproject.toml
git commit -m "chore: add GitHub repo URLs to package metadata"
```

- [ ] **Step 9: Connect and push to GitHub**

```bash
git remote add origin https://github.com/TU_USUARIO/faro.git
git push -u origin main
```

Expected: GitHub shows all your files at `https://github.com/TU_USUARIO/faro`.

---

## Task 7: Configure PyPI credentials

**Files:** `~/.pypirc` (your home directory)

- [ ] **Step 1: Generate a PyPI API token**

1. Log in at [https://pypi.org](https://pypi.org)
2. Go to **Account Settings → API tokens**
3. Click **Add API token**
4. Name: `faro-publish`
5. Scope: **Entire account** (first time; after first upload you can scope per project)
6. Click **Create token**
7. **Copy the token now** — it won't be shown again

- [ ] **Step 2: Create `~/.pypirc`**

On Windows, create at `C:\Users\Jahir\.pypirc`:

```ini
[distutils]
index-servers = pypi

[pypi]
username = __token__
password = pypi-PEGA_TU_TOKEN_AQUI
```

Replace `pypi-PEGA_TU_TOKEN_AQUI` with the token you copied.

- [ ] **Step 3: Verify the file is NOT tracked by git**

```bash
git status
```

`.pypirc` lives in your home directory (`C:\Users\Jahir\`), not in the repo — it will not appear in git status. Good.

---

## Task 8: Publish faro-core to PyPI

**Files:** none

- [ ] **Step 1: Navigate to ForecastingCore and rebuild (clean)**

```bash
cd C:\Users\Jahir\Documents\forecasting\ForecastingCore
rmdir /s /q dist
python -m build
```

- [ ] **Step 2: Upload to PyPI**

```bash
twine upload dist/*
```

Expected output:
```
Uploading distributions to https://upload.pypi.org/legacy/
Uploading faro_core-1.0.0-py3-none-any.whl
Uploading faro_core-1.0.0.tar.gz
View at: https://pypi.org/project/faro-core/1.0.0/
```

- [ ] **Step 3: Verify it's live**

Open `https://pypi.org/project/faro-core/` in your browser. The package page should appear with the README content.

- [ ] **Step 4: Test install from PyPI**

```bash
pip install faro-core
```

Expected: downloads and installs without errors.

---

## Task 9: Publish faro-prep to PyPI

**Files:** none

- [ ] **Step 1: Navigate to faro_prep_build and rebuild (clean)**

```bash
cd C:\Users\Jahir\Documents\forecasting\ForecastingCore\faro_prep_build
rmdir /s /q dist
python -m build
```

- [ ] **Step 2: Upload to PyPI**

```bash
twine upload dist/*
```

Expected output:
```
Uploading distributions to https://upload.pypi.org/legacy/
Uploading faro_prep-0.1.0-py3-none-any.whl
Uploading faro_prep-0.1.0.tar.gz
View at: https://pypi.org/project/faro-prep/0.1.0/
```

- [ ] **Step 3: Verify it's live**

Open `https://pypi.org/project/faro-prep/` in your browser.

- [ ] **Step 4: Test install from PyPI**

```bash
pip install faro-prep
```

Expected: downloads and installs without errors.

---

## Future: Publishing a New Version

Whenever you make changes and want to release a new version:

1. Bump `version` in the relevant `pyproject.toml` (e.g. `1.0.0` → `1.0.1`)
2. Commit the version bump: `git commit -m "chore: bump faro-core to 1.0.1"`
3. Tag the release: `git tag v1.0.1 && git push --tags`
4. Delete old dist: `rmdir /s /q dist`
5. Build: `python -m build`
6. Upload: `twine upload dist/*`
