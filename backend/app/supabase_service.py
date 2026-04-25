from __future__ import annotations

import logging
from functools import lru_cache

from app import settings

logger = logging.getLogger(__name__)


@lru_cache
def _service_client():
    if not settings.SUPABASE_DB_ENABLED:
        return None
    from supabase import create_client

    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


def log_chat_turn(
    *,
    user_id: str,
    user_message: str,
    assistant_message: str,
) -> None:
    client = _service_client()
    if client is None:
        return
    try:
        client.table("chat_messages").insert(
            [
                {"user_id": user_id, "role": "user", "content": user_message},
                {"user_id": user_id, "role": "assistant", "content": assistant_message},
            ]
        ).execute()
    except Exception:
        logger.exception("Supabase chat_messages insert failed (check SQL schema / RLS)")
