"""One HTTP seam for all providers so tests can patch a single function."""
from typing import Any, Optional
import requests

_TIMEOUT = 20


def _request(method: str, url: str, **kwargs) -> Any:
    kwargs.setdefault("timeout", _TIMEOUT)
    resp = requests.request(method, url, **kwargs)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def get_json(url: str, *, headers: Optional[dict] = None, params: Optional[dict] = None, auth=None) -> Any:
    return _request("GET", url, headers=headers, params=params, auth=auth)


def post_json(url: str, *, headers: Optional[dict] = None, json: Optional[dict] = None, auth=None) -> Any:
    return _request("POST", url, headers=headers, json=json, auth=auth)
