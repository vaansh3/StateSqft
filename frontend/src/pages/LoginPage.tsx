import type { CSSProperties, FormEvent } from "react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabase";
import { useSession } from "../useSession";

type AuthMode = "signin" | "signup";

const accent = "var(--accent)";
const ink = "var(--text)";
const muted = "var(--muted)";

const glassCard: CSSProperties = {
  width: "100%",
  maxWidth: 440,
  background: "var(--surface-raised)",
  borderRadius: 16,
  padding: "2rem 2.25rem",
  boxShadow: "0 8px 32px rgba(15, 23, 42, 0.1), 0 2px 8px rgba(15, 23, 42, 0.06)",
  border: "1px solid var(--border-subtle)",
};

export function LoginPage() {
  const navigate = useNavigate();
  const { session, loading } = useSession();
  const [mode, setMode] = useState<AuthMode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loading && session) {
      navigate("/", { replace: true });
    }
  }, [session, loading, navigate]);

  function switchMode(next: AuthMode) {
    setMode(next);
    setError(null);
    setInfo(null);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setBusy(true);
    if (mode === "signin") {
      const { error: err } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password,
      });
      setBusy(false);
      if (err) {
        setError(err.message);
        return;
      }
      navigate("/", { replace: true });
      return;
    }

    const { data, error: err } = await supabase.auth.signUp({
      email: email.trim(),
      password,
      options: {
        emailRedirectTo: `${window.location.origin}/`,
      },
    });
    setBusy(false);
    if (err) {
      setError(err.message);
      return;
    }
    if (data.session) {
      navigate("/", { replace: true });
      return;
    }
    setPassword("");
    setMode("signin");
    setError(null);
    setInfo(
      "Account created. Check your email for a confirmation link if your project requires it, then sign in.",
    );
  }

  if (loading) {
    return (
      <div className="login-shell" style={{ color: muted }}>
        <span style={{ fontSize: "0.95rem", letterSpacing: "0.02em" }}>Loading…</span>
      </div>
    );
  }

  return (
    <div className="login-shell">
      <div style={glassCard}>
        <h1
          style={{
            margin: 0,
            fontSize: "1.65rem",
            fontWeight: 700,
            lineHeight: 1.2,
            color: accent,
            textAlign: "center",
            letterSpacing: "-0.02em",
          }}
        >
          StateSqft
        </h1>
        <p
          style={{
            margin: "0.65rem 0 0",
            fontSize: "1rem",
            fontWeight: 600,
            color: ink,
            textAlign: "center",
            lineHeight: 1.4,
          }}
        >
          LLM-assisted metro for-sale inventory
        </p>
        <p style={{ margin: "0.35rem 0 0", fontSize: "0.82rem", color: muted, textAlign: "center" }}>
          Stevens Institute of Technology
        </p>

        <div
          style={{
            height: 1,
            margin: "1.35rem 0 1.25rem",
            background: "linear-gradient(90deg, transparent, rgba(15,23,42,0.12), transparent)",
          }}
        />

        <div
          style={{
            display: "flex",
            gap: 6,
            marginBottom: "1.25rem",
            padding: 5,
            borderRadius: 12,
            background: "var(--surface)",
            border: "1px solid var(--border-subtle)",
          }}
        >
          <button type="button" onClick={() => switchMode("signin")} style={tabStyle(mode === "signin")}>
            Sign in
          </button>
          <button type="button" onClick={() => switchMode("signup")} style={tabStyle(mode === "signup")}>
            Sign up
          </button>
        </div>

        {error ? (
          <p
            style={{
              color: "#fca5a5",
              fontSize: "0.85rem",
              marginBottom: "1rem",
              lineHeight: 1.45,
              textAlign: "center",
            }}
          >
            {error}
          </p>
        ) : null}
        {info ? (
          <p
            style={{
              color: "#86efac",
              fontSize: "0.85rem",
              marginBottom: "1rem",
              lineHeight: 1.45,
              textAlign: "center",
            }}
          >
            {info}
          </p>
        ) : null}

        <form onSubmit={onSubmit}>
          <label
            htmlFor="email"
            style={{ display: "block", fontSize: "0.75rem", fontWeight: 600, color: muted, marginBottom: 8 }}
          >
            Email
          </label>
          <input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={inputStyle}
          />
          <label
            htmlFor="password"
            style={{ display: "block", fontSize: "0.75rem", fontWeight: 600, color: muted, marginBottom: 8 }}
          >
            Password
          </label>
          <input
            id="password"
            type="password"
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
            required
            minLength={mode === "signup" ? 6 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={inputStyle}
          />
          <button type="submit" disabled={busy} style={submitStyle(busy)}>
            {mode === "signin" ? "Sign in" : "Create account"}
          </button>
        </form>

        <p style={{ margin: "1.35rem 0 0", fontSize: "0.82rem", color: muted, textAlign: "center", lineHeight: 1.5 }}>
          {mode === "signin" ? (
            <>
              No account?{" "}
              <button type="button" onClick={() => switchMode("signup")} style={linkButtonStyle}>
                Sign up
              </button>
            </>
          ) : (
            <>
              Already registered?{" "}
              <button type="button" onClick={() => switchMode("signin")} style={linkButtonStyle}>
                Sign in
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}

const inputStyle: CSSProperties = {
  width: "100%",
  padding: "0.7rem 0.85rem",
  borderRadius: 10,
  border: "1px solid var(--border-strong)",
  background: "var(--input-bg)",
  color: ink,
  marginBottom: "1rem",
  fontSize: "0.95rem",
  outline: "none",
};

function submitStyle(busy: boolean): CSSProperties {
  return {
    width: "100%",
    padding: "0.8rem 1rem",
    border: "none",
    borderRadius: 10,
    background: busy ? "rgba(30, 90, 140, 0.55)" : accent,
    color: "#f8fafc",
    fontWeight: 700,
    fontSize: "0.95rem",
    cursor: busy ? "not-allowed" : "pointer",
    marginTop: "0.25rem",
    boxShadow: busy ? "none" : "0 4px 16px rgba(30, 90, 140, 0.35)",
  };
}

function tabStyle(active: boolean): CSSProperties {
  return {
    flex: 1,
    padding: "0.55rem 0.75rem",
    border: "none",
    borderRadius: 9,
    fontWeight: 600,
    fontSize: "0.88rem",
    cursor: "pointer",
    transition: "background 0.15s ease, color 0.15s ease",
    background: active ? accent : "transparent",
    color: active ? "#f8fafc" : muted,
  };
}

const linkButtonStyle: CSSProperties = {
  border: "none",
  background: "none",
  padding: 0,
  color: accent,
  font: "inherit",
  fontWeight: 600,
  cursor: "pointer",
  textDecoration: "underline",
  textUnderlineOffset: 3,
};
