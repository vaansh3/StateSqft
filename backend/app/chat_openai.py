from __future__ import annotations

import json
import logging

from openai import AsyncOpenAI
from pydantic import ValidationError

from app import settings
from app.schemas_chat import ChatStructuredBlock, LLMChatStructuredPayload

logger = logging.getLogger(__name__)

_JSON_INSTRUCTION = (
    "\n\n**Output format:** Reply with **only** a single JSON object (no markdown fences, no prose outside JSON). "
    "Keys and types:\n"
    '- `"title"`: string — short user-facing headline.\n'
    '- `"summary"`: string — 2–4 sentences; state what the **data** shows when relevant, then the direct answer.\n'
    '- `"key_points"`: array of strings — 3–7 crisp bullets (use empty array if not helpful).\n'
    '- `"detail_markdown"`: string — main explanation in **Markdown** (headings, lists, tables allowed).\n'
    '- `"caveats"`: array of strings — limitations, scope, or when something is **not** in the dataset.\n'
    "All **numeric claims about the inventory CSV or matched metros** must come **only** from the DATA block in the user message."
)


def _system_prompt(*, has_inventory_charts: bool, inventory_direction_question: bool = False) -> str:
    base = (
        "You are **StateSqft**, an assistant for a web app backed by a **real metro for-sale inventory CSV**. "
        "**Every user message is prefixed with a DATA block** retrieved from that dataset (global snapshot at minimum; "
        "plus metro series, rankings, or charts context when applicable).\n"
        "**Conversation:** You may receive prior **user** and **assistant** messages. Treat short follow-ups as "
        "continuing the same thread (e.g. the user correcting a claim about a month/year, or referring to “that comparison” "
        "or “the previous answer”). The **current** question is the line after `User question:` in the **latest** user "
        "message. Still ground **all inventory numbers** only in the **DATA block** attached to that latest message.\n"
        "**Grounding rules:**\n"
        "- For **anything about the dataset** (coverage, dates, national totals, metro inventory, trends, rankings): use **only** "
        "the DATA block — do not invent numbers.\n"
        "- If the DATA block does not contain enough to answer, say so and say what **is** in the data.\n"
        "- You may use **general knowledge** only where the user asks broader questions; **clearly label** what is from the CSV vs general reasoning.\n"
        "- Be accurate, concise, and helpful. If unsure, say so.\n"
    )
    if has_inventory_charts:
        charts_prompt = (
            base
            + "\n**This turn:** the app shows **interactive metro inventory charts** synced with the DATA block. "
            "For **numbers about matched metros or the CSV**, use **only** that DATA — the UI charts reflect the same figures.\n"
            "If the DATA includes **### Requested months (spot inventory for comparison)**, use those values for direct "
            "month-to-month comparisons (do not claim a month is missing if it appears there).\n"
            "In **`detail_markdown`**, use this **Markdown** section order (after a one-line intro inside detail if you want):\n"
            "## Summary\n"
            "If the DATA includes a **cross-metro ranking** table (best place / which metro to sell / top markets), "
            "**start with a numbered or bulleted list of full metro names** from that table (best ranks first), then "
            "brief interpretation. If the DATA is **one or more named metros** with time series, summarize those metros "
            "normally.\n"
            "## Output\n"
            "Interpret what the UI shows: **line** chart = inventory over the selected period (dashed line = period mean "
            "when shown). **Pie** chart = proportional view of the same summary numbers (per metro: min / mean / max / "
            "latest in that period; for a **ranking** turn: metros vs prior six-month average — see chart subtitle). "
            "Inventory counts only — not prices or demand.\n"
            "## Takeaways\n"
            "3–5 bullets when inventory is central; otherwise adapt length to the question.\n"
            "**End `detail_markdown` after Takeaways.** Do **not** add `## How to read the charts`, `## Timing perspective`, "
            "or `## Verdict` in text. If the UI shows **per-metro** line + pie charts, a **Verdict** panel (Yes / No / Unclear + reason) "
            "appears after those charts — do not contradict it. If the turn is only a **ranking** chart, **no** Verdict "
            "panel — do not tell the user to open one.\n"
        )
        if inventory_direction_question:
            charts_prompt += (
                "\n**Direction / next month:** the DATA may include **### Cross-metro + full-history context**. "
                "Use it together with the charts. In your answer, frame **next month** as **uncertain** informed momentum, "
                "not a guaranteed forecast.\n"
            )
        return charts_prompt + _JSON_INSTRUCTION
    return (
        base
        + "\n**This turn:** **no** metro inventory charts are attached (no city/region was matched, or the message did not "
        "select a metro). The DATA block still includes the **global dataset snapshot** — use it for file-level facts.\n"
        "In **`detail_markdown`**, use a **natural** structure for the question (headings optional). Do **not** force "
        "`## Summary` / `## Output` / `## Takeaways` unless you are interpreting hypothetical charts (there are none).\n"
        "If the user wants metro-level numbers, suggest they name a metro (e.g. “Austin, TX”).\n"
    ) + _JSON_INSTRUCTION


def assistant_log_content(structured: ChatStructuredBlock, detail: str) -> str:
    parts: list[str] = []
    if structured.title.strip():
        parts.append(structured.title.strip())
    if structured.summary.strip():
        parts.append(structured.summary.strip())
    if structured.key_points:
        parts.append("\n".join(f"- {p}" for p in structured.key_points))
    if detail.strip():
        parts.append(detail.strip())
    if structured.caveats:
        parts.append("Notes:\n" + "\n".join(f"- {c}" for c in structured.caveats))
    return "\n\n".join(parts).strip()


async def complete_chat(
    *,
    user_message: str,
    data_context: str | None = None,
    has_inventory_charts: bool = False,
    inventory_direction_question: bool = False,
    history: list[dict[str, str]] | None = None,
) -> tuple[str, ChatStructuredBlock]:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    user_block = user_message
    if data_context:
        user_block = f"{data_context}\n\n---\n\nUser question:\n{user_message}"
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": _system_prompt(
                has_inventory_charts=has_inventory_charts,
                inventory_direction_question=inventory_direction_question,
            ),
        },
    ]
    if history:
        for hm in history:
            role = hm.get("role")
            content = (hm.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_block})
    resp = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=messages,
        temperature=0.55 if not has_inventory_charts else 0.4,
        max_tokens=4096,
        response_format={"type": "json_object"},
    )
    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return "", ChatStructuredBlock(
            title="No response",
            summary="The model returned an empty reply.",
            caveats=[],
        )

    try:
        payload = LLMChatStructuredPayload.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        logger.warning("Structured chat parse failed, using raw text: %s", e)
        return raw, ChatStructuredBlock(
            title="Answer",
            summary="",
            key_points=[],
            caveats=["The model did not return valid structured JSON; showing raw output below."],
        )

    structured = ChatStructuredBlock(
        title=payload.title.strip(),
        summary=payload.summary.strip(),
        key_points=[p.strip() for p in payload.key_points if isinstance(p, str) and p.strip()],
        caveats=[c.strip() for c in payload.caveats if isinstance(c, str) and c.strip()],
    )
    detail = payload.detail_markdown.strip()
    return detail, structured
