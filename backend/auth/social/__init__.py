"""Optional sign-in with Google, Microsoft and Apple.

Off unless the instance operator enables it (`SOCIAL_LOGIN_ENABLED` plus a
provider's credentials, in /instalacion or the environment). `providers.py`
talks to the providers; `flow.py` owns state, accounts and the token handoff;
the routes are in `backend/api/v1/social_auth.py`.
"""
