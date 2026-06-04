# PyPI Publication & GitHub Repo — Design Spec

**Date:** 2026-06-03  
**Status:** Approved

---

## Goal

Publish two Python libraries from the ForecastingCore codebase to PyPI so external users can install them via `pip`. Simultaneously push the full Faro platform to a GitHub repository. Local imports in the main project remain unchanged.

---

## Packages to Publish

| PyPI Name   | Source Directory                          | Current Internal Name  |
|-------------|-------------------------------------------|------------------------|
| `faro-core` | `ForecastingCore/forecasting_core/`       | `forecasting_core`     |
| `faro-prep` | `ForecastingCore/forecastlib/`            | `forecastlib`          |

---

## Section 1: Package Structure

### faro-core

Uses the existing `pyproject.toml` in `ForecastingCore/`. Only the `name` field changes.

```
ForecastingCore/
├── forecasting_core/     ← source code, untouched
├── pyproject.toml        ← name = "faro-core"
└── README.md             ← new, required by PyPI
```

**No source files are moved or renamed.** Local imports (`from forecasting_core import ...`) continue to work exactly as today.

### faro-prep

A new build subdirectory is created alongside the existing source. It points up one level to find the package.

```
ForecastingCore/
├── forecastlib/          ← source code, untouched
└── faro_prep_build/
    ├── pyproject.toml    ← name = "faro-prep", where = [".."]
    └── README.md
```

The `where = [".."]` directive tells setuptools to find packages in `ForecastingCore/` (one level up), and `include = ["forecastlib", "forecastlib.*"]` limits the build to only the `forecastlib` package.

**No source files are moved or renamed.** Local imports (`from forecastlib import ...`) continue to work exactly as today.

---

## Section 2: GitHub Repository

The entire Faro platform is pushed to a single GitHub repository named `faro`.

```
faro/                     ← GitHub repo root
├── ForecastingCore/      ← both libraries (source)
├── backend/              ← FastAPI backend
├── Frontend/             ← Next.js frontend
├── CLAUDE.md
├── .gitignore            ← new: excludes __pycache__, .env, node_modules, dist/, *.egg-info
└── README.md             ← new: project overview
```

The local import structure is preserved — the backend continues to import from `ForecastingCore/forecasting_core` and `ForecastingCore/forecastlib` via local paths. No `sys.path` changes required as long as the repo is cloned in full.

---

## Section 3: Manual Publishing Workflow

### One-time setup

1. Log in to [pypi.org](https://pypi.org) (account already created)
2. Go to **Account Settings → API tokens → Add API token**
3. Create a token scoped to "Entire account" for the first publish
4. Save credentials to `~/.pypirc`:

```ini
[distutils]
index-servers = pypi

[pypi]
username = __token__
password = pypi-<your-token-here>
```

5. Install build tools:
```bash
pip install build twine
```

### Publishing faro-core

```bash
cd ForecastingCore
python -m build
twine upload dist/*
```

### Publishing faro-prep

```bash
cd ForecastingCore/faro_prep_build
python -m build
twine upload dist/*
```

### Publishing a new version

1. Bump `version` in the relevant `pyproject.toml`
2. Delete old `dist/` contents
3. Run `python -m build` then `twine upload dist/*`

---

## Section 4: Files to Create / Modify

| File | Action | Notes |
|------|--------|-------|
| `ForecastingCore/pyproject.toml` | Modify | Change `name` to `faro-core`, add metadata |
| `ForecastingCore/README.md` | Create | PyPI landing page for faro-core |
| `ForecastingCore/faro_prep_build/pyproject.toml` | Create | Build config for faro-prep |
| `ForecastingCore/faro_prep_build/README.md` | Create | PyPI landing page for faro-prep |
| `.gitignore` | Create | Repo root, covers Python + Node + env files |
| `README.md` | Create | Repo root, Faro platform overview |

---

## Constraints

- Source code directories (`forecasting_core/`, `forecastlib/`) must not be renamed or moved.
- No changes to import statements anywhere in the project.
- Secrets (`.env`, API keys) must not be committed to GitHub.
- `pyproject.toml` for `faro-prep` must use `where = [".."]` so it can live in a subdirectory while finding the package above it.
