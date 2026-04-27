import { useCallback, useEffect, useRef, useState } from "react";
import { AssistantMessage } from "../components/AssistantMessage";
import { supabase } from "../lib/supabase";
import type { ChatApiResponse, ChatStructuredBlock, MetroViz, VerdictPayload } from "../types/chat";

const API_URL = import.meta.env.VITE_API_URL;

if (!API_URL) {
  throw new Error("Missing VITE_API_URL. Set it in frontend/.env.local");
}

type AssistantContent =
  | { type: "plain"; text: string }
  | {
      type: "rich";
      reply: string;
      structured: ChatStructuredBlock;
      dataset_note: string;
      data_window: string;
      metros: MetroViz[];
    };

type Line = { role: "user"; text: string } | { role: "assistant"; content: AssistantContent };

const MAX_HISTORY_MESSAGES = 24;

function assistantPlainText(c: AssistantContent): string {
  if (c.type === "plain") return c.text;
  const parts: string[] = [];
  if (c.structured.title?.trim()) parts.push(c.structured.title.trim());
  if (c.structured.summary?.trim()) parts.push(c.structured.summary.trim());
  if (c.structured.key_points?.length) {
    parts.push(c.structured.key_points.map((p) => `• ${p}`).join("\n"));
  }
  if (c.reply?.trim()) parts.push(c.reply.trim());
  if (c.structured.caveats?.length) {
    parts.push("Notes:\n" + c.structured.caveats.map((x) => `• ${x}`).join("\n"));
  }
  return parts.join("\n\n").slice(0, 10000);
}

function linesToApiHistory(lines: Line[]): { role: "user" | "assistant"; content: string }[] {
  const out: { role: "user" | "assistant"; content: string }[] = [];
  for (const l of lines) {
    if (l.role === "user") {
      out.push({ role: "user", content: l.text.slice(0, 12000) });
    } else {
      const t = assistantPlainText(l.content);
      if (t.trim()) out.push({ role: "assistant", content: t });
    }
  }
  return out.slice(-MAX_HISTORY_MESSAGES);
}

function formatApiDetail(detail: unknown): string {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        typeof item === "object" && item !== null && "msg" in item
          ? String((item as { msg: string }).msg)
          : JSON.stringify(item)
      )
      .join(" ");
  }
  if (typeof detail === "object") return JSON.stringify(detail);
  return String(detail);
}

function parseVerdictPayload(v: unknown): VerdictPayload | undefined {
  if (!v || typeof v !== "object") return undefined;
  const o = v as Record<string, unknown>;
  if (o.answer !== "yes" && o.answer !== "no" && o.answer !== "unclear") return undefined;
  if (typeof o.headline !== "string" || typeof o.reason !== "string") return undefined;
  return {
    answer: o.answer,
    headline: o.headline,
    reason: o.reason,
    basis: Array.isArray(o.basis) ? o.basis.filter((x): x is string => typeof x === "string") : [],
  };
}

function parseChatResponse(j: unknown): ChatApiResponse | null {
  if (!j || typeof j !== "object") return null;
  const o = j as Record<string, unknown>;
  if (typeof o.reply !== "string") return null;
  const st = o.structured;
  const rec =
    st !== null && st !== undefined && typeof st === "object"
      ? (st as Record<string, unknown>)
      : null;
  const structured: ChatStructuredBlock = rec
    ? {
        title: typeof rec.title === "string" ? rec.title : "",
        summary: typeof rec.summary === "string" ? rec.summary : "",
        key_points: Array.isArray(rec.key_points)
          ? rec.key_points.filter((x: unknown): x is string => typeof x === "string")
          : [],
        caveats: Array.isArray(rec.caveats)
          ? rec.caveats.filter((x: unknown): x is string => typeof x === "string")
          : [],
      }
    : { title: "", summary: "", key_points: [], caveats: [] };

  return {
    reply: o.reply,
    structured,
    dataset_note: typeof o.dataset_note === "string" ? o.dataset_note : "",
    data_window: typeof o.data_window === "string" ? o.data_window : "",
    metros: Array.isArray(o.metros)
      ? (o.metros as MetroViz[]).map((m) => ({
          ...m,
          selling_insights: Array.isArray(m.selling_insights) ? m.selling_insights : [],
          verdict: parseVerdictPayload(m.verdict),
        }))
      : [],
  };
}

const HELP_PROMPTS: { category: string; prompts: string[] }[] = [
  {
    category: "Metro Inventory Lookup",
    prompts: [
      "What is the current inventory in Austin, TX?",
      "Show me inventory trends for Miami, FL over the last year.",
      "How has inventory changed in Seattle, WA since 2022?",
    ],
  },
  {
    category: "Compare Markets",
    prompts: [
      "Compare inventory in Phoenix, AZ vs Denver, CO.",
      "Which has more inventory right now: Dallas, TX or Houston, TX?",
      "Show me New York, NY vs Los Angeles, CA inventory side by side.",
    ],
  },
  {
    category: "Rankings & Top Markets",
    prompts: [
      "Which metros have the highest inventory right now?",
      "What are the top 5 buyer-friendly markets by inventory?",
      "Which metros saw the biggest inventory increase in the past 6 months?",
    ],
  },
  {
    category: "Trends & Direction",
    prompts: [
      "Is inventory in Chicago, IL going up or down?",
      "Which metros are showing the fastest inventory growth this year?",
      "Has national for-sale inventory recovered since 2022?",
    ],
  },
  {
    category: "Dataset & Coverage",
    prompts: [
      "What metros are covered in this dataset?",
      "What date range does the data cover?",
      "What does this dataset measure exactly?",
    ],
  },
];

export function ChatPage() {
  const [lines, setLines] = useState<Line[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [who, setWho] = useState("");
  const [helpOpen, setHelpOpen] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  const refreshWho = useCallback(async () => {
    const { data: s } = await supabase.auth.getSession();
    const token = s.session?.access_token;
    if (!token) return;
    const r = await fetch(`${API_URL}/api/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (r.ok) {
      const j = (await r.json()) as { email?: string; user_id?: string };
      setWho(j.email || j.user_id || "");
    }
  }, []);

  useEffect(() => {
    void refreshWho();
  }, [refreshWho]);

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [lines]);

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    const historyPayload = linesToApiHistory(lines);
    setInput("");
    setLines((prev) => [...prev, { role: "user", text }]);
    setSending(true);
    const { data: s } = await supabase.auth.getSession();
    const token = s.session?.access_token;
    if (!token) {
      setLines((prev) => [
        ...prev,
        { role: "assistant", content: { type: "plain", text: "Not signed in. Go to /login." } },
      ]);
      setSending(false);
      return;
    }
    let content: AssistantContent = {
      type: "plain",
      text: "",
    };
    try {
      const r = await fetch(`${API_URL}/api/chat`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message: text, history: historyPayload }),
      });
      if (!r.ok) {
        try {
          const err = (await r.json()) as { detail?: unknown };
          const msg = formatApiDetail(err.detail);
          content = {
            type: "plain",
            text: msg || `Request failed (${r.status})`,
          };
        } catch {
          content = {
            type: "plain",
            text: `Request failed (${r.status}). Is the API running at ${API_URL}?`,
          };
        }
      } else {
        const j = await r.json();
        const parsed = parseChatResponse(j);
        if (parsed) {
          const rawReply = (parsed.reply || "").trim();
          const hasStructuredBody =
            !!(parsed.structured?.title?.trim() || parsed.structured?.summary?.trim());
          const reply =
            rawReply ||
            (hasStructuredBody ? "" : "(The model returned an empty reply.)");
          content = {
            type: "rich",
            reply,
            structured: parsed.structured || { title: "", summary: "", key_points: [], caveats: [] },
            dataset_note: parsed.dataset_note || "",
            data_window: parsed.data_window || "",
            metros: parsed.metros || [],
          };
        } else {
          content = { type: "plain", text: "Unexpected response from server." };
        }
      }
    } catch (e) {
      content = {
        type: "plain",
        text:
          e instanceof TypeError
            ? `Network error — could not reach ${API_URL}. (${e.message})`
            : `Something went wrong: ${e instanceof Error ? e.message : String(e)}`,
      };
    }
    setLines((prev) => [...prev, { role: "assistant", content }]);
    setSending(false);
  }

  async function signOut() {
    await supabase.auth.signOut();
    window.location.href = "/login";
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: "100vh",
        background: "var(--bg)",
        color: "var(--text)",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0.85rem 1.25rem",
          borderBottom: "1px solid var(--border-subtle)",
          background: "var(--panel)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <h1 style={{ margin: 0, fontSize: "1rem", lineHeight: 1.25, color: "var(--text)" }}>
            StateSqft
          </h1>
          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            title="Show example prompts"
            style={{
              width: 22,
              height: 22,
              borderRadius: "50%",
              border: "1px solid var(--border-strong)",
              background: "var(--surface-raised)",
              color: "var(--accent)",
              fontWeight: 700,
              fontSize: "0.78rem",
              lineHeight: 1,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            ?
          </button>
        </div>
        <div>
          <span style={{ color: "var(--muted)", marginRight: 12, fontSize: "0.9rem" }}>{who}</span>
          <button
            type="button"
            onClick={() => void signOut()}
            style={{
              background: "transparent",
              border: "1px solid var(--border-strong)",
              color: "var(--text)",
              padding: "0.35rem 0.65rem",
              borderRadius: 6,
              cursor: "pointer",
              fontSize: "0.85rem",
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      {sending ? (
        <div
          className="chat-progress-track"
          role="progressbar"
          aria-busy="true"
          aria-valuetext="Generating response"
        >
          <div className="chat-progress-indeterminate" />
        </div>
      ) : null}
      <main
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          maxWidth: 960,
          margin: "0 auto",
          width: "100%",
          padding: "1rem 1.25rem 1.25rem",
          minHeight: 0,
        }}
      >
        <div
          ref={logRef}
          style={{
            flex: 1,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "0.85rem",
            marginBottom: "0.75rem",
          }}
        >
          {lines.length === 0 ? (
            <p style={{ color: "var(--muted)" }}>Ask about metro inventory…</p>
          ) : null}
          {lines.map((l, i) => {
            if (l.role === "user") {
              return (
                <div
                  key={i}
                  style={{
                    alignSelf: "flex-end",
                    maxWidth: "88%",
                    padding: "0.65rem 0.85rem",
                    borderRadius: 10,
                    lineHeight: 1.45,
                    whiteSpace: "pre-wrap",
                    background: "var(--bubble-user)",
                    border: "1px solid var(--border-subtle)",
                    color: "var(--text)",
                  }}
                >
                  {l.text}
                </div>
              );
            }
            if (l.content.type === "plain") {
              return (
                <div
                  key={i}
                  style={{
                    alignSelf: "flex-start",
                    maxWidth: "92%",
                    padding: "0.65rem 0.85rem",
                    borderRadius: 10,
                    lineHeight: 1.45,
                    whiteSpace: "pre-wrap",
                    background: "var(--bubble-ai)",
                    border: "1px solid var(--border-subtle)",
                    color: "var(--text)",
                  }}
                >
                  {l.content.text}
                </div>
              );
            }
            return (
              <div key={i} style={{ alignSelf: "stretch", width: "100%" }}>
                <AssistantMessage
                  reply={l.content.reply}
                  structured={l.content.structured}
                  dataset_note={l.content.dataset_note}
                  data_window={l.content.data_window}
                  metros={l.content.metros}
                />
              </div>
            );
          })}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
          <textarea
            rows={2}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            placeholder="Message…"
            style={{
              flex: 1,
              minHeight: 44,
              borderRadius: 10,
              border: "1px solid var(--border-strong)",
              background: "var(--input-bg)",
              color: "var(--text)",
              padding: "0.65rem 0.75rem",
              fontFamily: "inherit",
              fontSize: "0.95rem",
            }}
          />
          <button
            type="button"
            disabled={sending}
            onClick={() => void send()}
            style={{
              padding: "0.65rem 1rem",
              border: "none",
              borderRadius: 10,
              background: "var(--accent)",
              color: "#f8fafc",
              fontWeight: 600,
              cursor: sending ? "not-allowed" : "pointer",
              opacity: sending ? 0.6 : 1,
            }}
          >
            Send
          </button>
        </div>
      </main>

      {helpOpen && (
        <div
          onClick={() => setHelpOpen(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15,23,42,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
            padding: "1rem",
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "var(--surface-raised)",
              border: "1px solid var(--border-subtle)",
              borderRadius: 12,
              padding: "1.5rem",
              maxWidth: 560,
              width: "100%",
              maxHeight: "80vh",
              overflowY: "auto",
              boxShadow: "0 8px 32px rgba(15,23,42,0.18)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: "1.1rem",
              }}
            >
              <h2 style={{ margin: 0, fontSize: "1rem", color: "var(--text)" }}>
                Example prompts
              </h2>
              <button
                type="button"
                onClick={() => setHelpOpen(false)}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  fontSize: "1.25rem",
                  color: "var(--muted)",
                  lineHeight: 1,
                  padding: "0 0.25rem",
                }}
              >
                ×
              </button>
            </div>
            <p style={{ margin: "0 0 1rem", fontSize: "0.85rem", color: "var(--muted)" }}>
              This app is backed by a metro for-sale housing inventory dataset covering{" "}
              <strong>928 metros</strong> from <strong>March 2018 to February 2026</strong>. Click
              any prompt to use it.
            </p>
            {HELP_PROMPTS.map((group) => (
              <div key={group.category} style={{ marginBottom: "1rem" }}>
                <p
                  style={{
                    margin: "0 0 0.4rem",
                    fontSize: "0.78rem",
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    color: "var(--accent)",
                  }}
                >
                  {group.category}
                </p>
                <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                  {group.prompts.map((p) => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => {
                        setInput(p);
                        setHelpOpen(false);
                      }}
                      style={{
                        textAlign: "left",
                        background: "var(--surface)",
                        border: "1px solid var(--border-subtle)",
                        borderRadius: 8,
                        padding: "0.5rem 0.75rem",
                        cursor: "pointer",
                        fontSize: "0.875rem",
                        color: "var(--text)",
                        lineHeight: 1.4,
                      }}
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
