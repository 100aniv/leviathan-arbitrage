"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";

const BASE_URL = process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8000";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch(`${BASE_URL}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError((data as { detail?: string }).detail ?? "Login failed");
        return;
      }
      const data = await res.json() as { access_token: string };
      localStorage.setItem("leviathan_token", data.access_token);
      // Also set a cookie so the Next.js middleware can verify auth server-side
      const secureFlag = window.location.protocol === "https:" ? "; Secure" : "";
      document.cookie = `leviathan_token=${data.access_token}; path=/; SameSite=Strict${secureFlag}`;
      router.push("/");
    } catch {
      setError("Network error — engine unreachable");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-terminal-bg">
      <div className="w-full max-w-sm">
        <div className="card p-8 space-y-6">
          <div className="text-center">
            <h1 className="text-lg font-mono font-semibold text-terminal-text tracking-widest uppercase">
              LEVIATHAN
            </h1>
            <p className="text-xs font-mono text-terminal-subtle mt-1">War Room Access</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1">
              <label className="text-xs font-mono text-terminal-subtle uppercase tracking-wider">
                Username
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoComplete="username"
                className="w-full bg-terminal-surface border border-terminal-border rounded px-3 py-2 text-sm font-mono text-terminal-text focus:outline-none focus:border-profit"
              />
            </div>

            <div className="space-y-1">
              <label className="text-xs font-mono text-terminal-subtle uppercase tracking-wider">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
                className="w-full bg-terminal-surface border border-terminal-border rounded px-3 py-2 text-sm font-mono text-terminal-text focus:outline-none focus:border-profit"
              />
            </div>

            {error && (
              <p className="text-xs font-mono text-loss">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2 px-4 bg-profit/10 border border-profit/40 text-profit text-sm font-mono rounded hover:bg-profit/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? "AUTHENTICATING…" : "LOGIN"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
