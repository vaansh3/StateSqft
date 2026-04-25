from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class TimeSeriesChartSpec(BaseModel):
    kind: Literal["line"] = "line"
    title: str
    subtitle: str = ""
    x_labels: list[str] = Field(description="Short labels for X axis")
    values: list[float | None] = Field(description="Y values aligned with x_labels")
    y_axis_label: str = "For-sale inventory (homes)"
    mean_reference: float | None = Field(
        default=None,
        description="Horizontal reference line (period mean)",
    )


class PieChartSpec(BaseModel):
    """Proportional view of summary levels (same numbers as former bar chart; slice size ∝ value)."""

    kind: Literal["pie"] = "pie"
    title: str
    subtitle: str = ""
    categories: list[str]
    values: list[float | None]


ChartSpec = Annotated[
    Union[TimeSeriesChartSpec, PieChartSpec],
    Field(discriminator="kind"),
]


class VerdictPayload(BaseModel):
    """Structured Yes/No verdict (inventory-only heuristics). Shown in UI after charts."""

    answer: Literal["yes", "no", "unclear"]
    headline: str
    reason: str
    basis: list[str] = Field(
        default_factory=list,
        description="Plain steps: rules and numbers used (optional; UI may hide)",
    )


class MetroVizPayload(BaseModel):
    region_id: int
    region_name: str
    region_type: str
    size_rank: int
    period_label: str
    timing_as_of: str | None = Field(
        default=None,
        description="Latest month in file for this metro (optional)",
    )
    selling_insights: list[str] = Field(
        default_factory=list,
        description="Deprecated; kept empty for API compatibility",
    )
    metric_mean: float | None = None
    metric_min: float | None = None
    metric_max: float | None = None
    metric_latest: float | None = None
    metric_avg_6m: float | None = None
    metric_vs_avg_6m_pct: float | None = None
    charts: list[ChartSpec] = Field(default_factory=list)
    verdict: VerdictPayload | None = Field(
        default=None,
        description="Yes/No/Unclear from latest inventory vs prior months (+ trend); after charts in UI",
    )


class ChatStructuredBlock(BaseModel):
    """Structured fields for a user-friendly layout (body text lives in `reply` on ChatResponseViz)."""

    title: str = ""
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class LLMChatStructuredPayload(BaseModel):
    """Parsed JSON object from the model (internal)."""

    title: str = Field(default="", description="Short headline for the answer")
    summary: str = Field(default="", description="2–4 sentence overview")
    key_points: list[str] = Field(default_factory=list, description="Short bullet strings")
    detail_markdown: str = Field(
        default="",
        description="Main answer in Markdown (sections per system instructions when charts apply)",
    )
    caveats: list[str] = Field(
        default_factory=list,
        description="Limits, scope, or separation of data vs general knowledge",
    )


class ChatResponseViz(BaseModel):
    reply: str
    structured: ChatStructuredBlock = Field(default_factory=ChatStructuredBlock)
    dataset_note: str = ""
    data_window: str = ""
    metros: list[MetroVizPayload] = Field(default_factory=list)
