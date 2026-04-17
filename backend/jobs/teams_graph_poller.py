from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from config import settings
from services.ms_graph_service import ms_graph_service
from services.qdrant_service import qdrant_service


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _load_state() -> dict[str, Any]:
    path = settings.teams_state_path
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = settings.teams_state_path
    if not path:
        return
    _ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    s = s or ""
    s = _TAG_RE.sub("", s)
    return s.replace("&nbsp;", " ").replace("&amp;", "&").strip()


def _stable_id(chat_id: str, message_id: str) -> str:
    return hashlib.sha256(f"teams|chat|{chat_id}|{message_id}".encode("utf-8")).hexdigest()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_users() -> list[str]:
    raw = (settings.teams_user_ids or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


async def _list_user_chats(user_id: str) -> list[dict[str, Any]]:
    url = f"https://graph.microsoft.com/v1.0/users/{user_id}/chats"
    chats: list[dict[str, Any]] = []
    page = await ms_graph_service.get_page(url, params={"$top": 50})
    chats.extend(page.values)
    while page.next_link:
        page = await ms_graph_service.get_page(page.next_link)
        chats.extend(page.values)
    return chats


async def _sync_chat_messages(chat_id: str, *, delta_url: str | None) -> tuple[list[dict[str, Any]], str | None]:
    # Delta endpoint returns pages; final page returns @odata.deltaLink.
    url = delta_url or f"https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/delta"
    page = await ms_graph_service.get_page(url, params={"$top": 50} if not delta_url else None)
    msgs: list[dict[str, Any]] = []
    msgs.extend(page.values)
    while page.next_link:
        page = await ms_graph_service.get_page(page.next_link)
        msgs.extend(page.values)
    return msgs, page.delta_link


async def run_teams_graph_poll_loop(stop_event: asyncio.Event, interval_minutes: int) -> None:
    state = _load_state()
    delta_by_chat: dict[str, str] = {}
    if isinstance(state.get("delta_by_chat"), dict):
        delta_by_chat = {str(k): str(v) for k, v in state.get("delta_by_chat", {}).items() if v}

    users = _parse_users()
    if not users:
        print("[kairos] Teams sync enabled but TEAMS_USER_IDS is empty; nothing to sync.")

    while not stop_event.is_set():
        try:
            for user in users:
                chats = await _list_user_chats(user)
                for c in chats:
                    chat_id = str(c.get("id") or "").strip()
                    if not chat_id:
                        continue

                    topic = c.get("topic")

                    msgs, new_delta = await _sync_chat_messages(chat_id, delta_url=delta_by_chat.get(chat_id))
                    if new_delta:
                        delta_by_chat[chat_id] = new_delta

                    texts: list[str] = []
                    payloads: list[dict[str, Any]] = []
                    ids: list[str] = []

                    for m in msgs:
                        mid = str(m.get("id") or "").strip()
                        if not mid:
                            continue

                        created = str(m.get("createdDateTime") or _iso_now())
                        body = m.get("body") or {}
                        content = _strip_html(str(body.get("content") or ""))
                        if not content:
                            continue

                        from_user = ((m.get("from") or {}).get("user") or {}) if isinstance(m.get("from"), dict) else {}
                        from_id = str(from_user.get("id") or "").strip()
                        from_name = str(from_user.get("displayName") or "").strip()

                        who = from_name or from_id or "unknown"
                        text = f"[Teams] ({created}) {who}: {content}"

                        texts.append(text)
                        payloads.append(
                            {
                                "timestamp": created,
                                "source": "teams",
                                "service": f"teams_chat:{chat_id}",
                                "severity": None,
                                "file_path": None,
                                "function_name": None,
                                "line_start": 1,
                                "link": str(m.get("webUrl") or ""),
                                "chat_id": chat_id,
                                "message_id": mid,
                                "from_id": from_id,
                                "from_name": from_name,
                            }
                        )
                        ids.append(_stable_id(chat_id, mid))

                    if texts:
                        await qdrant_service.upsert_documents(texts, payloads, ids=ids)

            _save_state({"delta_by_chat": delta_by_chat, "updated_at": _iso_now()})
        except Exception as exc:
            print(f"[kairos] Teams Graph poll warning: {exc}")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(5, interval_minutes * 60))
        except asyncio.TimeoutError:
            pass

