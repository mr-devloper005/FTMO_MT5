"""Financial Modeling Prep "stable" API client (research data only).

Endpoints were verified against the live API on 2026-10-08 with this account's
key (FMP's documentation site returns 403 to this machine). The key goes in the
``apikey`` request header, so it never appears in a URL, a log line, a raw file
or an exception message.

Plan behaviour seen on this key (HTTP status, FMP message):
  401 "Invalid API KEY"                    -> FmpAuthError
  402 "Restricted Endpoint"                -> FmpPlanRestricted (e.g. transcripts, press releases)
  402 "Premium Query Parameter ... limit"  -> FmpPlanRestricted (this plan: limit <= 5, some period=quarter)
  429 "Limit Reach"                        -> FmpQuotaExceeded at once (quota used up; no retries)
  429 / 5xx                                -> retried with backoff, then FmpRateLimited / FmpHTTPError
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

from rag.config import fmp_api_key

BASE = "https://financialmodelingprep.com/stable/"
DEFAULT_RPS = 5.0
MAX_LIMIT = 5                      # this plan rejects limit > 5


class FmpError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, endpoint: str = ""):
        super().__init__(message)
        self.status = status
        self.endpoint = endpoint


class FmpAuthError(FmpError):
    """Missing or invalid API key."""


class FmpPlanRestricted(FmpError):
    """Endpoint or parameter not included in the current FMP subscription."""


class FmpRateLimited(FmpError):
    """Still throttled after retries."""


class FmpQuotaExceeded(FmpRateLimited):
    """429 'Limit Reach': the plan's request quota is used up. Retrying cannot help; stop and try later."""


class FmpHTTPError(FmpError):
    """Any other non-success response."""


def public_url(endpoint: str, params: Optional[Dict[str, Any]] = None) -> str:
    """The request URL as it can be shown to people and stored as source_url: never contains the key."""
    clean = {k: v for k, v in (params or {}).items() if k.lower() != "apikey"}
    return BASE + endpoint + (("?" + urlencode(clean)) if clean else "")


class FmpClient:
    def __init__(self, api_key: Optional[str] = None, max_rps: float = DEFAULT_RPS, raw_dir: Optional[Path] = None,
                 session: Optional[requests.Session] = None, max_retries: int = 4, sleep=time.sleep):
        self._key = api_key or fmp_api_key()
        self.gap = 1.0 / max_rps
        self.raw_dir = raw_dir
        self.max_retries = max_retries
        self.sleep = sleep
        self.session = session or requests.Session()
        self._last = 0.0
        self.calls = 0

    def __repr__(self) -> str:                                   # never show the key
        return f"FmpClient(calls={self.calls})"

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "<FMP_API_KEY>") if self._key else text

    def get(self, endpoint: str, **params: Any) -> Any:
        """GET /stable/<endpoint>. Returns parsed JSON (usually a list); raises a typed FmpError."""
        params = {k: v for k, v in params.items() if v is not None}
        for attempt in range(self.max_retries + 1):
            wait = self._last + self.gap - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            try:
                r = self.session.get(BASE + endpoint, params=params, headers={"apikey": self._key}, timeout=60)
            except requests.RequestException as e:
                if attempt < self.max_retries:
                    self.sleep(2 ** attempt)
                    continue
                raise FmpHTTPError(self._redact(f"network error calling {endpoint}: {type(e).__name__}"),
                                   None, endpoint) from None
            status = r.status_code
            if status == 429 and "limit reach" in r.text.lower():
                raise FmpQuotaExceeded(f"{endpoint}: FMP request quota used up (HTTP 429 'Limit Reach'); "
                                       f"try again after the plan's quota resets", status, endpoint)
            if status == 429 or status >= 500:
                if attempt < self.max_retries:
                    retry_after = r.headers.get("Retry-After")
                    self.sleep(float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt)
                    continue
                cls = FmpRateLimited if status == 429 else FmpHTTPError
                raise cls(f"{endpoint}: HTTP {status} after {self.max_retries} retries", status, endpoint)
            body = self._json(r)
            message = body.get("Error Message") if isinstance(body, dict) else None
            if status == 401 or (message and "invalid api key" in message.lower()):
                raise FmpAuthError(f"{endpoint}: FMP rejected the API key (HTTP {status})", status, endpoint)
            if status in (402, 403) or (message and any(w in message for w in ("Restricted", "Premium", "subscription"))):
                raise FmpPlanRestricted(f"{endpoint}: not available on the current FMP plan "
                                        f"({self._redact((message or '').split(' please visit')[0][:160])})",
                                        status, endpoint)
            if status != 200 or message:
                raise FmpHTTPError(f"{endpoint}: HTTP {status} {self._redact((message or r.text)[:200])}",
                                   status, endpoint)
            self._save_raw(endpoint, params, body)
            return body
        raise FmpHTTPError(f"{endpoint}: gave up", None, endpoint)              # pragma: no cover

    @staticmethod
    def _json(r: requests.Response) -> Any:
        try:
            return r.json()
        except ValueError:
            return {"Error Message": r.text[:300]} if r.status_code != 200 else None

    def _save_raw(self, endpoint: str, params: Dict[str, Any], body: Any) -> None:
        """Keep every successful response (no key inside) under state/raw/fmp/<SYMBOL>/."""
        if not self.raw_dir:
            return
        sym = str(params.get("symbol") or params.get("symbols") or "_all").upper()
        tag = hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:10]
        path = self.raw_dir / sym / f"{endpoint.replace('/', '_')}__{tag}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"endpoint": endpoint, "params": params, "fetched_at": time.time(),
                                    "url": public_url(endpoint, params), "body": body}))
