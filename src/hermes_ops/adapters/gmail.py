"""Gmail REST adapter. No policy here, no MCP here — just Google calls."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from googleapiclient.discovery import build

HERMES_LABELS: list[str] = [
    "Hermes/Action",
    "Hermes/Waiting",
    "Hermes/FYI",
    "Hermes/Reference",
    "Hermes/Noise",
]


class GmailAdapter:
    def __init__(self, creds) -> None:
        self.svc = build("gmail", "v1", credentials=creds, cache_discovery=False)

    # ---------------------------------------------------------------- identity
    def profile(self) -> dict:
        return self.svc.users().getProfile(userId="me").execute()

    # ------------------------------------------------------------------ labels
    def list_labels(self) -> list[dict]:
        return self.svc.users().labels().list(userId="me").execute().get("labels", [])

    def label_maps(self) -> tuple[dict[str, str], dict[str, str]]:
        """Return (name->id, id->name)."""
        name_to_id: dict[str, str] = {}
        id_to_name: dict[str, str] = {}
        for label in self.list_labels():
            name_to_id[label["name"]] = label["id"]
            id_to_name[label["id"]] = label["name"]
        return name_to_id, id_to_name

    def ensure_labels(self, names: list[str] | None = None) -> dict[str, str]:
        wanted = names or HERMES_LABELS
        existing = {label["name"]: label["id"] for label in self.list_labels()}
        result: dict[str, str] = {}
        for name in wanted:
            if name in existing:
                result[name] = existing[name]
                continue
            created = (
                self.svc.users()
                .labels()
                .create(
                    userId="me",
                    body={
                        "name": name,
                        "labelListVisibility": "labelShow",
                        "messageListVisibility": "show",
                    },
                )
                .execute()
            )
            result[name] = created["id"]
        return result

    # -------------------------------------------------------------- retrieval
    def search(self, query: str, limit: int) -> tuple[list[dict], int]:
        """Return (message stubs, resultSizeEstimate). Paginates up to `limit`."""
        collected: list[dict] = []
        page_token: str | None = None
        estimate = 0
        while len(collected) < limit:
            resp = (
                self.svc.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=min(100, limit - len(collected)),
                    pageToken=page_token,
                )
                .execute()
            )
            estimate = resp.get("resultSizeEstimate", estimate)
            collected.extend(resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return collected[:limit], estimate

    def get_message(self, message_id: str, fmt: str = "metadata") -> dict:
        kwargs: dict[str, Any] = {"userId": "me", "id": message_id, "format": fmt}
        if fmt == "metadata":
            kwargs["metadataHeaders"] = [
                "From",
                "To",
                "Subject",
                "Date",
                "Message-Id",
                "In-Reply-To",
                "References",
            ]
        return self.svc.users().messages().get(**kwargs).execute()

    def get_thread(self, thread_id: str, fmt: str = "full") -> dict:
        return (
            self.svc.users()
            .threads()
            .get(userId="me", id=thread_id, format=fmt)
            .execute()
        )

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        resp = (
            self.svc.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        return base64.urlsafe_b64decode(resp["data"])

    # ---------------------------------------------------------------- mutation
    def modify(
        self,
        message_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> dict:
        return (
            self.svc.users()
            .messages()
            .modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": add or [], "removeLabelIds": remove or []},
            )
            .execute()
        )

    # ------------------------------------------------------------------ drafts
    def _raw(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None,
        headers: dict[str, str],
    ) -> str:
        msg = EmailMessage()
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        for key, value in headers.items():
            if value:
                msg[key] = value
        msg.set_content(body)
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    def create_draft(
        self,
        thread_id: str,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        raw = self._raw(to, subject, body, cc, headers or {})
        return (
            self.svc.users()
            .drafts()
            .create(
                userId="me",
                body={"message": {"raw": raw, "threadId": thread_id}},
            )
            .execute()
        )

    def update_draft(
        self,
        draft_id: str,
        thread_id: str,
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        raw = self._raw(to, subject, body, cc, headers or {})
        return (
            self.svc.users()
            .drafts()
            .update(
                userId="me",
                id=draft_id,
                body={"message": {"raw": raw, "threadId": thread_id}},
            )
            .execute()
        )
