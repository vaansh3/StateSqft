from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import APIError
from pydantic import BaseModel, Field

from app import settings
from app.chat_openai import assistant_log_content, complete_chat
from app.deps import UserContext, require_supabase_user
from app.inventory_data import is_inventory_trend_or_forecast_question, retrieve_inventory_for_chat
from app.schemas_chat import ChatResponseViz
from app.supabase_service import log_chat_turn

logger = logging.getLogger(__name__)

app = FastAPI(title="StateSqft API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/me")
async def api_me(user: Annotated[UserContext, Depends(require_supabase_user)]):
    return {"user_id": user.id, "email": user.email}


class ChatHistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=12000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    history: list[ChatHistoryTurn] = Field(
        default_factory=list,
        max_length=32,
        description="Prior turns (oldest first). Used for date/metro context and LLM continuity.",
    )


def _history_as_retrieval_context(history: list[ChatHistoryTurn]) -> str:
    lines: list[str] = []
    for h in history[-16:]:
        label = "User" if h.role == "user" else "Assistant"
        lines.append(f"{label}: {h.content[:4000]}")
    return "\n".join(lines)


def _history_openai_messages(history: list[ChatHistoryTurn]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for h in history[-24:]:
        c = h.content.strip()
        if not c:
            continue
        out.append({"role": h.role, "content": c[:12000]})
    return out


@app.post("/api/chat", response_model=ChatResponseViz)
async def api_chat(
    body: ChatRequest,
    user: Annotated[UserContext, Depends(require_supabase_user)],
):
    if not settings.OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="OpenAI is not configured. Set OPENAI_API_KEY in backend/.env.",
        )
    conv_ctx = _history_as_retrieval_context(body.history)
    data_block, metros, data_window, dataset_note = retrieve_inventory_for_chat(
        body.message.strip(),
        conversation_context=conv_ctx,
    )
    asks_direction = is_inventory_trend_or_forecast_question(body.message.strip())
    try:
        reply, structured = await complete_chat(
            user_message=body.message.strip(),
            data_context=data_block,
            has_inventory_charts=len(metros) > 0,
            inventory_direction_question=asks_direction and len(metros) > 0,
            history=_history_openai_messages(body.history),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except APIError as e:
        logger.warning("OpenAI API error: %s", e)
        msg = getattr(e, "message", None) or str(e)
        if len(msg) > 500:
            msg = msg[:500] + "…"
        raise HTTPException(status_code=502, detail=msg) from e
    except Exception:
        logger.exception("OpenAI chat failed")
        raise HTTPException(
            status_code=502,
            detail="OpenAI request failed. Check backend logs, API key, and OPENAI_MODEL.",
        ) from None

    if settings.SUPABASE_DB_ENABLED:
        log_chat_turn(
            user_id=user.id,
            user_message=body.message.strip(),
            assistant_message=assistant_log_content(structured, reply),
        )
    return ChatResponseViz(
        reply=reply,
        structured=structured,
        dataset_note=dataset_note,
        data_window=data_window,
        metros=metros,
    )
