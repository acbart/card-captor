"""Helpers for building safe, application-internal redirect URLs.

Redirect targets are always assembled from a fixed allow-list of page names plus
integer identifiers, so no request data can ever influence the host or scheme of
a redirect.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi.responses import RedirectResponse

#: Sub-pages of an activity that may be used as redirect targets.
ACTIVITY_PAGES: dict[str, str] = {
    "": "",
    "import": "/import",
    "cards": "/cards",
    "review": "/review",
    "finalize": "/finalize",
}


def activity_url(activity_id: int, page: str = "", message: str = "") -> str:
    """Build an internal URL such as ``/activities/3/review``."""
    if page not in ACTIVITY_PAGES:
        raise ValueError(f"Unknown activity page: {page!r}")
    url = "/activities/%d%s" % (int(activity_id), ACTIVITY_PAGES[page])
    if message:
        url = "%s?message=%s" % (url, quote(str(message), safe=""))
    return url


def redirect_to_activity(
    activity_id: int, page: str = "", message: str = "", status_code: int = 303
) -> RedirectResponse:
    """Redirect to an activity page built solely from literals and an integer."""
    return RedirectResponse(
        url=activity_url(activity_id, page, message), status_code=status_code
    )


def redirect_home(status_code: int = 303) -> RedirectResponse:
    """Redirect to the dashboard."""
    return RedirectResponse(url="/", status_code=status_code)
