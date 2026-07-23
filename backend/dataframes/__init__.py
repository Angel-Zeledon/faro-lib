"""
Data-file boundary (pandas-boundary refactor).

This package is the ONLY place in the application layer that imports pandas.
Every function takes a path / bytes / DataFrame and returns plain Python
(list[dict], dict, list[str]) — a DataFrame never crosses back out to a
consumer. Keeping pandas here (plus utils/temporal_agg.py and workers/runner.py)
lets the rest of backend/ stay pandas-free, which the architecture test in
tests/test_no_pandas_in_backend.py enforces.
"""
