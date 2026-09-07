"""Google Calendar REST adapter. No policy here, no MCP here.

Everything works with the narrow ``calendar.events`` scope: event list/get/
insert/patch/delete and free/busy. ``calendarList`` is never called (it needs
``calendar.readonly``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from googleapiclient.discovery import build


class CalendarAdapter:
    def __init__(self, creds) -> None:
        self.svc = build("calendar", "v3", credentials=creds, cache_discovery=False)

    def probe(self, calendar_id: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return (
            self.svc.events()
            .list(
                calendarId=calendar_id,
                maxResults=1,
                singleEvents=True,
                timeMin=now,
                orderBy="startTime",
            )
            .execute()
        )

    def list_events(
        self,
        calendar_id: str,
        time_min: str,
        time_max: str,
        max_results: int = 250,
    ) -> list[dict]:
        items: list[dict] = []
        page_token: str | None = None
        while True:
            resp = (
                self.svc.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=min(2500, max_results),
                    pageToken=page_token,
                )
                .execute()
            )
            items.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token or len(items) >= max_results:
                break
        return items[:max_results]

    def get_event(self, calendar_id: str, event_id: str) -> dict:
        return (
            self.svc.events()
            .get(calendarId=calendar_id, eventId=event_id)
            .execute()
        )

    def insert_event(self, calendar_id: str, body: dict[str, Any]) -> dict:
        return (
            self.svc.events()
            .insert(calendarId=calendar_id, body=body)
            .execute()
        )

    def patch_event(
        self, calendar_id: str, event_id: str, body: dict[str, Any]
    ) -> dict:
        return (
            self.svc.events()
            .patch(calendarId=calendar_id, eventId=event_id, body=body)
            .execute()
        )

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        self.svc.events().delete(calendarId=calendar_id, eventId=event_id).execute()

    def free_busy(
        self, time_min: str, time_max: str, calendar_ids: list[str]
    ) -> dict[str, list[dict]]:
        resp = (
            self.svc.freebusy()
            .query(
                body={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "items": [{"id": cid} for cid in calendar_ids],
                }
            )
            .execute()
        )
        return {
            cid: cal.get("busy", [])
            for cid, cal in resp.get("calendars", {}).items()
        }
