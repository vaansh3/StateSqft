import ReactMarkdown from "react-markdown";
import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type {
  ChartSpec,
  ChatStructuredBlock,
  MetroViz,
  PieChartSpec,
  TimeSeriesChartSpec,
  VerdictPayload,
} from "../types/chat";

const axisStyle = { fill: "#64748b", fontSize: 11 };
const gridStyle = { stroke: "rgba(15, 23, 42, 0.08)" };
const tooltipStyle = {
  backgroundColor: "#f2f6fb",
  border: "1px solid rgba(15, 23, 42, 0.14)",
  borderRadius: 8,
  color: "#0f172a",
  boxShadow: "0 4px 12px rgba(15, 23, 42, 0.1)",
};

const PIE_COLORS = ["#4a8bb8", "#5b9fd4", "#7eb8e8", "#9cc0d8", "#c4d8e8", "#a8b8c8"];

function fmt(n: number | null | undefined) {
  if (n == null || Number.isNaN(n)) return "—";
  return Math.round(n).toLocaleString();
}

function LineViz({ spec }: { spec: TimeSeriesChartSpec }) {
  const data = spec.x_labels.map((name, i) => ({
    name,
    inventory: spec.values[i] ?? null,
  }));
  const mean = spec.mean_reference;

  return (
    <div style={{ marginTop: "0.75rem" }}>
      <div style={{ fontSize: "0.8rem", color: "var(--muted)", marginBottom: 6 }}>{spec.subtitle}</div>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridStyle.stroke} />
          <XAxis dataKey="name" tick={axisStyle} interval="preserveStartEnd" />
          <YAxis
            tick={axisStyle}
            tickFormatter={(v) => (v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : `${(v / 1e3).toFixed(0)}k`)}
            width={48}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(value) => [
              typeof value === "number" ? value.toLocaleString() : "—",
              spec.y_axis_label || "Inventory",
            ]}
          />
          {mean != null && !Number.isNaN(mean) && (
            <ReferenceLine
              y={mean}
              stroke="#5b8eb8"
              strokeDasharray="5 5"
              label={{ value: "Period mean", fill: "#64748b", fontSize: 11 }}
            />
          )}
          <Line
            type="monotone"
            dataKey="inventory"
            stroke="#1e5a8c"
            strokeWidth={2}
            dot={{ r: 3, fill: "#1e5a8c" }}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
      <div style={{ fontSize: "0.75rem", color: "var(--muted)", marginTop: 6 }}>
        Y-axis: {spec.y_axis_label || "inventory"}. Dashed line = average over the selected period.
      </div>
    </div>
  );
}

function PieViz({ spec }: { spec: PieChartSpec }) {
  const data = spec.categories.map((name, i) => {
    const v = spec.values[i];
    const n = v != null && !Number.isNaN(v) ? Math.max(v, 0) : 0;
    return { name, value: n };
  });
  const total = data.reduce((s, d) => s + d.value, 0);
  if (total <= 0) {
    return (
      <div style={{ marginTop: "0.75rem", fontSize: "0.85rem", color: "var(--muted)" }}>No values for pie chart.</div>
    );
  }
  return (
    <div style={{ marginTop: "0.75rem" }}>
      {spec.subtitle ? (
        <div style={{ fontSize: "0.8rem", color: "var(--muted)", marginBottom: 6 }}>{spec.subtitle}</div>
      ) : null}
      <ResponsiveContainer width="100%" height={280}>
        <PieChart margin={{ top: 8, right: 8, left: 8, bottom: 8 }}>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            outerRadius={100}
            label={({ name, percent }) => `${name}: ${(percent * 100).toFixed(0)}%`}
          >
            {data.map((_, i) => (
              <Cell key={`cell-${i}`} fill={PIE_COLORS[i % PIE_COLORS.length]} stroke="rgba(0,0,0,0.2)" />
            ))}
          </Pie>
          <Tooltip
            contentStyle={tooltipStyle}
            formatter={(value) =>
              typeof value === "number"
                ? [value.toLocaleString(undefined, { maximumFractionDigits: 0 }), ""]
                : ["—", ""]
            }
          />
          <Legend wrapperStyle={{ fontSize: 12, color: "#64748b" }} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

function ChartBlock({ chart }: { chart: ChartSpec }) {
  if (chart.kind === "line") {
    return (
      <div
        style={{
          background: "var(--surface-raised)",
          borderRadius: 12,
          padding: "1rem",
          border: "1px solid var(--border-subtle)",
        }}
      >
        <div style={{ fontWeight: 600, fontSize: "0.95rem", color: "var(--text)" }}>{chart.title}</div>
        <LineViz spec={chart} />
      </div>
    );
  }
  return (
    <div
      style={{
        background: "var(--surface-raised)",
        borderRadius: 12,
        padding: "1rem",
        border: "1px solid var(--border-subtle)",
      }}
    >
      <div style={{ fontWeight: 600, fontSize: "0.95rem", color: "var(--text)" }}>{chart.title}</div>
      <PieViz spec={chart} />
    </div>
  );
}

function verdictBarPercents(answer: VerdictPayload["answer"]): { yes: number; no: number; label: string } {
  if (answer === "yes") return { yes: 100, no: 0, label: "Leans Yes" };
  if (answer === "no") return { yes: 0, no: 100, label: "Leans No" };
  return { yes: 50, no: 50, label: "Unclear — even split (heuristic inconclusive)" };
}

function VerdictPanel({ verdict }: { verdict: VerdictPayload }) {
  const { yes, no, label } = verdictBarPercents(verdict.answer);
  const yesW = Math.max(yes, 0);
  const noW = Math.max(no, 0);

  return (
    <div
      style={{
        marginTop: "1rem",
        padding: "1rem 1.1rem",
        borderRadius: 12,
        background: "rgba(30, 90, 140, 0.09)",
        border: "1px solid rgba(30, 90, 140, 0.22)",
      }}
    >
      <div
        style={{
          fontSize: "0.72rem",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: "var(--muted)",
          marginBottom: 8,
          fontWeight: 600,
        }}
      >
        Verdict
      </div>
      <div style={{ fontSize: "0.8rem", color: "var(--muted)", marginBottom: 10 }}>{label}</div>
      {verdict.headline ? (
        <div style={{ fontSize: "0.88rem", fontWeight: 600, color: "var(--text)", marginBottom: 10, lineHeight: 1.35 }}>
          {verdict.headline}
        </div>
      ) : null}

      <div
        style={{
          display: "flex",
          height: 36,
          borderRadius: 10,
          overflow: "hidden",
          width: "100%",
          border: "1px solid var(--border-subtle)",
          marginBottom: 8,
        }}
      >
        <div
          style={{
            width: `${yesW}%`,
            minWidth: yesW > 0 ? 2 : 0,
            background: "linear-gradient(180deg, rgba(90, 160, 105, 0.85), rgba(60, 120, 75, 0.9))",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#e8f5eb",
            fontWeight: 700,
            fontSize: "0.8rem",
            transition: "width 0.25s ease",
          }}
          title={`Yes ${yes}%`}
        >
          {yesW >= 18 ? `Yes ${yes}%` : yesW > 0 ? `${yes}%` : ""}
        </div>
        <div
          style={{
            width: `${noW}%`,
            minWidth: noW > 0 ? 2 : 0,
            background: "linear-gradient(180deg, rgba(200, 95, 95, 0.88), rgba(150, 55, 55, 0.92))",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#fdeaea",
            fontWeight: 700,
            fontSize: "0.8rem",
            transition: "width 0.25s ease",
          }}
          title={`No ${no}%`}
        >
          {noW >= 18 ? `No ${no}%` : noW > 0 ? `${no}%` : ""}
        </div>
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          fontSize: "0.78rem",
          color: "var(--muted)",
          marginBottom: 12,
        }}
      >
        <span>
          <strong style={{ color: "#9cc9a8" }}>Yes</strong> {yes}%
        </span>
        <span>
          <strong style={{ color: "#e8a0a0" }}>No</strong> {no}%
        </span>
      </div>

      <div style={{ fontSize: "0.78rem", color: "var(--muted)", marginBottom: 10, lineHeight: 1.45 }}>
        Inventory-only heuristic for sell/buy timing (not financial advice). Bar shows how the rule landed, not a market
        probability model.
      </div>
      <div style={{ fontSize: "0.92rem", lineHeight: 1.55, color: "var(--text)" }}>
        <ReactMarkdown>{verdict.reason}</ReactMarkdown>
      </div>
    </div>
  );
}

function MetricGrid({ m }: { m: MetroViz }) {
  const items: { k: string; v: string }[] = [
    { k: "Mean", v: fmt(m.metric_mean) },
    { k: "Latest", v: fmt(m.metric_latest) },
    { k: "Min", v: fmt(m.metric_min) },
    { k: "Max", v: fmt(m.metric_max) },
    { k: "Avg (last ≤6 mo)", v: fmt(m.metric_avg_6m) },
  ];
  if (m.metric_vs_avg_6m_pct != null && !Number.isNaN(m.metric_vs_avg_6m_pct)) {
    items.push({
      k: "Latest vs avg",
      v: `${m.metric_vs_avg_6m_pct >= 0 ? "+" : ""}${m.metric_vs_avg_6m_pct.toFixed(1)}%`,
    });
  }
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
        gap: 8,
        marginBottom: "1rem",
      }}
    >
      {items.map((x) => (
        <div
          key={x.k}
          style={{
            background: "rgba(30, 90, 140, 0.1)",
            borderRadius: 8,
            padding: "0.5rem 0.65rem",
            border: "1px solid rgba(30, 90, 140, 0.2)",
          }}
        >
          <div style={{ fontSize: "0.7rem", color: "var(--muted)", textTransform: "uppercase" }}>{x.k}</div>
          <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>{x.v}</div>
        </div>
      ))}
    </div>
  );
}

type Props = {
  reply: string;
  structured?: ChatStructuredBlock;
  dataset_note: string;
  data_window: string;
  metros: MetroViz[];
};

export function AssistantMessage({ reply, structured, dataset_note, data_window, metros }: Props) {
  const title = structured?.title?.trim();
  const summary = structured?.summary?.trim();
  const keyPoints = structured?.key_points?.filter((p) => p.trim()) ?? [];
  const caveats = structured?.caveats?.filter((c) => c.trim()) ?? [];

  return (
    <div
      style={{
        alignSelf: "stretch",
        width: "100%",
        maxWidth: 920,
        padding: "1rem 1.1rem",
        borderRadius: 12,
        background: "var(--bubble-ai)",
        border: "1px solid var(--border-subtle)",
        color: "var(--text)",
      }}
    >
      {(dataset_note || data_window) && (
        <div
          style={{
            fontSize: "0.78rem",
            color: "var(--muted)",
            marginBottom: "0.85rem",
            paddingBottom: "0.65rem",
            borderBottom: "1px solid var(--border-subtle)",
          }}
        >
          {data_window ? (
            <div>
              <strong style={{ color: "var(--accent)" }}>Period:</strong> {data_window}
            </div>
          ) : null}
          {dataset_note ? (
            <div style={{ marginTop: 4 }}>
              <strong style={{ color: "var(--accent)" }}>Data:</strong> {dataset_note}
            </div>
          ) : null}
        </div>
      )}

      {title ? (
        <h2 style={{ margin: "0 0 0.65rem", fontSize: "1.15rem", fontWeight: 700, lineHeight: 1.3 }}>{title}</h2>
      ) : null}
      {summary ? (
        <div
          style={{
            marginBottom: keyPoints.length || reply.trim() ? "0.85rem" : 0,
            padding: "0.65rem 0.85rem",
            borderRadius: 10,
            background: "rgba(30, 90, 140, 0.08)",
            border: "1px solid rgba(30, 90, 140, 0.18)",
            fontSize: "0.92rem",
            lineHeight: 1.5,
            color: "var(--text)",
          }}
        >
          {summary}
        </div>
      ) : null}
      {keyPoints.length > 0 ? (
        <ul style={{ margin: "0 0 0.85rem", paddingLeft: "1.25rem", lineHeight: 1.55, fontSize: "0.9rem" }}>
          {keyPoints.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      ) : null}

      {reply.trim() ? (
        <div className="assistant-md">
          <ReactMarkdown>{reply}</ReactMarkdown>
        </div>
      ) : null}

      {caveats.length > 0 ? (
        <div
          style={{
            marginTop: reply.trim() ? "0.85rem" : 0,
            paddingTop: reply.trim() ? "0.75rem" : 0,
            borderTop: reply.trim() ? "1px solid var(--border-subtle)" : "none",
            fontSize: "0.82rem",
            color: "var(--muted)",
            lineHeight: 1.5,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 6, color: "var(--text)", fontSize: "0.78rem" }}>Notes</div>
          <ul style={{ margin: 0, paddingLeft: "1.2rem" }}>
            {caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {metros.length > 0 ? (
        <div style={{ marginTop: "1.25rem" }}>
          <h3
            style={{
              fontSize: "0.85rem",
              textTransform: "uppercase",
              letterSpacing: "0.04em",
              color: "var(--muted)",
              margin: "0 0 0.75rem",
            }}
          >
            Visualizations
          </h3>
          {metros.map((metro) => (
            <div
              key={metro.region_id}
              style={{
                marginBottom: "1.5rem",
                paddingBottom: "1.25rem",
                borderBottom: "1px solid var(--border-subtle)",
              }}
            >
              <h4 style={{ margin: "0 0 0.35rem", fontSize: "1.05rem" }}>{metro.region_name}</h4>
              <div style={{ fontSize: "0.8rem", color: "var(--muted)", marginBottom: "0.75rem" }}>
                {metro.region_type.toLowerCase() === "ranking" ? (
                  <>Cross-metro ranking · {metro.period_label}</>
                ) : (
                  <>
                    {metro.region_type.toUpperCase()} · rank {metro.size_rank} · {metro.period_label}
                  </>
                )}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                {metro.charts.map((c, i) => (
                  <ChartBlock key={`${metro.region_id}-${c.kind}-${i}`} chart={c} />
                ))}
              </div>
              {metro.verdict ? <VerdictPanel verdict={metro.verdict} /> : null}
              {metro.region_type.toLowerCase() === "ranking" ? null : <MetricGrid m={metro} />}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
