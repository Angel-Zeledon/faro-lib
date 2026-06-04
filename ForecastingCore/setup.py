"""
setup.py — makes forecasting_core installable via:
    pip install -e .
"""

from setuptools import setup, find_packages

setup(
    name="forecasting_core",
    version="1.0.0",
    description="Enterprise-grade time-series forecasting library",
    author="Angel Zeledon",
    packages=find_packages(include=["forecasting_core", "forecasting_core.*"]),
    python_requires=">=3.9",
    install_requires=[
        "pandas>=1.5",
        "numpy>=1.23",
        "scikit-learn>=1.1",
        "lightgbm>=3.3",
        "xgboost>=1.7",
        "prophet>=1.1",
        "statsmodels>=0.13",
        "scipy>=1.9",
        "holidays>=0.20",
    ],
    extras_require={
        "api": ["fastapi>=0.100", "uvicorn[standard]>=0.22", "python-multipart"],
        "dl": ["tensorflow>=2.11"],
        "dev": ["pytest", "black", "ruff"],
    },
)
