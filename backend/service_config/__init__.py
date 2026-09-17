"""Service configuration: what this deployment has, what is on, and what is off.

    from backend.service_config.resolver import effective
    cfg = effective(tenant_id)        # or effective() for the instance scope
    if not cfg.deepseek_api_key:
        ...                           # degrade, and say so

Read `registry.py` first — it is the source of truth every other module here
derives from, including `.env.example` and `docs/configuration.md`.
"""
