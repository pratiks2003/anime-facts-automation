"""Daily early-warning check (runs on GitHub Actions only - never call Meta from a laptop).

- Instagram token: still valid? when does it expire? (Meta debug_token)
- Instagram account: can we read it? (catches "API access blocked" before a Reel fails)

Problems become an owner note (Agent_Activity__c, Needs_Owner) on the admin site, with exactly what to do.

  python -m pipeline.health
"""

from __future__ import annotations

import sys
import time
from typing import List, Optional, Tuple

from integrations.instagram import InstagramAPIError, client_from_env
from integrations.salesforce import SalesforceClient

WARN_DAYS = 10


def log(msg: str) -> None:
    print(f"[health] {msg}", file=sys.stderr, flush=True)


def token_problem(debug: dict, now: Optional[float] = None) -> Optional[Tuple[str, str]]:
    """(severity, message) from a debug_token response, or None when healthy."""
    now = now or time.time()
    data = debug.get("data") or {}
    if data and not data.get("is_valid", False):
        return "critical", ("The Instagram access token is no longer valid, so Reels cannot be posted. Generate a new "
                            "system-user token in Meta Business Settings and update the IG_ACCESS_TOKEN GitHub secret.")
    expires = int(data.get("expires_at") or 0)  # 0 = never expires
    if expires:
        days = (expires - now) / 86400
        if days <= WARN_DAYS:
            return ("critical" if days <= 3 else "warning"), (
                f"The Instagram access token expires in {max(0, days):.0f} day(s). Generate a new system-user token "
                "(choose 'Never' expiry) in Meta Business Settings and update the IG_ACCESS_TOKEN GitHub secret.")
    return None


def check() -> List[Tuple[str, str]]:
    problems: List[Tuple[str, str]] = []
    ig = client_from_env()
    try:
        debug = ig._request("GET", "debug_token", {"input_token": ig._token})
        p = token_problem(debug)
        if p:
            problems.append(p)
        else:
            exp = int((debug.get("data") or {}).get("expires_at") or 0)
            log("token ok, " + ("never expires" if not exp else f"expires in {(exp - time.time()) / 86400:.0f} days"))
    except InstagramAPIError as exc:
        if exc.code == 190:
            problems.append(("critical", "The Instagram access token was rejected (expired or revoked). Generate a new "
                                         "system-user token and update the IG_ACCESS_TOKEN GitHub secret."))
        else:
            log(f"debug_token unavailable ({exc}); checking the account instead")
    try:
        ig.account()
        log("Instagram account reachable")
    except InstagramAPIError as exc:
        blocked = exc.subcode == 5258005 or "blocked" in str(exc).lower()
        problems.append(("critical", (
            "Meta has blocked API access for the app. Open Meta for Developers, follow the prompt to restore access, "
            "then reply here." if blocked else f"Instagram API check failed: {exc}. Reels may fail to post.")))
    return problems


def notify(sf: Optional[SalesforceClient], problems: List[Tuple[str, str]]) -> None:
    for severity, message in problems:
        log(f"{severity}: {message}")
        if sf is None:
            continue
        sf.request("POST", "sobjects/Agent_Activity__c", {
            "Agent__c": "Ops", "Action__c": "Health check", "Details__c": message[:5000],
            "Needs_Owner__c": True, "Severity__c": severity})


def main(argv=None) -> int:
    problems = check()
    notify(SalesforceClient.from_env(), problems)
    if not problems:
        log("all healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
