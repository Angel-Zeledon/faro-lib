"""What-if scenario simulation (PENDIENTES #7).

`service.py` owns persistence (the `scenarios` table) and the BASE vs SCENARIO
comparison; `bridge.py` is the only place that talks to ForecastingCore's
ScenarioEngine. No forecast math lives in this package.
"""
