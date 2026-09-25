"""ActivityWatch ingest: read tracked events through its local REST API.

ActivityWatch must be installed and running on this machine (it is the
capture layer; this project only reads from it, never writes).
API docs: the server listens at http://localhost:5600/api/0/
"""

from datetime import datetime, time, timedelta

import requests

DEFAULT_BASE_URL = "http://localhost:5600/api/0/"

# Window events from these apps are dropped when a web-watcher event covers
# the same time span -- otherwise browser time would be counted twice
# (once as "chrome", once as the actual tab URL).
BROWSER_APPS = {
    "chrome", "firefox", "msedge", "edge", "safari",
    "brave", "opera", "vivaldi", "chromium",
}


class ActivityWatchError(Exception):
    """Raised when ActivityWatch cannot be reached or understood."""


def _parse_ts(value):
    # ActivityWatch emits ISO-8601, usually UTC ("...Z" or "+00:00").
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class ActivityWatchClient:
    def __init__(self, base_url=DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/") + "/"

    def _get(self, path, params=None):
        url = self.base_url + path.lstrip("/")
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.ConnectionError:
            raise ActivityWatchError(
                "Could not reach ActivityWatch at %s. Is ActivityWatch "
                "running on this computer? Start it and try again -- or "
                "run the pipeline with --demo to try everything without it."
                % self.base_url
            )
        except requests.Timeout:
            raise ActivityWatchError(
                "ActivityWatch at %s did not answer in time. Is it overloaded "
                "or still starting up?" % self.base_url
            )

    def get_buckets(self):
        """Return the dict of bucket_id -> bucket info."""
        return self._get("buckets")

    def get_events(self, bucket_id, start_iso, end_iso):
        """Return raw events for one bucket between two ISO timestamps."""
        return self._get(
            "buckets/%s/events" % bucket_id,
            params={"start": start_iso, "end": end_iso},
        )

    def fetch_day(self, day):
        """Fetch one calendar day (a datetime.date).

        Returns (events, afk_seconds) where each event is a dict:
            {"ts": iso str, "duration": seconds (float),
             "app": str, "title": str, "url": str|None}

        Reads the aw-watcher-window buckets (apps + window titles),
        aw-watcher-web-* buckets (browser tab URLs, when the web
        extension/watcher is installed) and aw-watcher-afk (idle time).
        """
        start_dt = datetime.combine(day, time.min).astimezone()
        end_dt = start_dt + timedelta(days=1)
        start_iso, end_iso = start_dt.isoformat(), end_dt.isoformat()

        buckets = self.get_buckets()
        window_ids = [b for b in buckets if b.startswith("aw-watcher-window_")]
        web_ids = [b for b in buckets if b.startswith("aw-watcher-web-")]
        afk_ids = [b for b in buckets if b.startswith("aw-watcher-afk_")]

        if not window_ids and not web_ids:
            raise ActivityWatchError(
                "ActivityWatch answered, but no window or web watcher buckets "
                "were found. Enable the watchers in ActivityWatch and try again."
            )

        events = []
        for bucket_id in window_ids:
            for raw in self.get_events(bucket_id, start_iso, end_iso):
                data = raw.get("data") or {}
                events.append({
                    "ts": raw.get("timestamp"),
                    "duration": float(raw.get("duration") or 0),
                    "app": data.get("app") or "",
                    "title": data.get("title") or "",
                    "url": None,
                })

        web_intervals = []
        for bucket_id in web_ids:
            # bucket ids look like "aw-watcher-web-chrome_<hostname>"
            browser = bucket_id[len("aw-watcher-web-"):].split("_")[0] or "browser"
            for raw in self.get_events(bucket_id, start_iso, end_iso):
                data = raw.get("data") or {}
                url = data.get("url")
                if not url:
                    continue
                start = _parse_ts(raw.get("timestamp"))
                end = start + timedelta(seconds=float(raw.get("duration") or 0))
                web_intervals.append((start, end))
                events.append({
                    "ts": raw.get("timestamp"),
                    "duration": float(raw.get("duration") or 0),
                    "app": browser,
                    "title": data.get("title") or "",
                    "url": url,
                })

        afk_seconds = 0.0
        for bucket_id in afk_ids:
            for raw in self.get_events(bucket_id, start_iso, end_iso):
                if (raw.get("data") or {}).get("status") == "afk":
                    afk_seconds += float(raw.get("duration") or 0)

        # Drop browser window events covered by a web event (no double count).
        merged = []
        for event in events:
            if event["url"] is None and event["app"].lower() in BROWSER_APPS:
                start = _parse_ts(event["ts"])
                end = start + timedelta(seconds=event["duration"])
                if any(not (end <= ws or start >= we) for ws, we in web_intervals):
                    continue
            merged.append(event)

        merged.sort(key=lambda e: e["ts"] or "")
        return merged, afk_seconds
