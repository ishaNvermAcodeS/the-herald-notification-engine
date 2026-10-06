"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { DEMO_USERS, getSession, login } from "@/lib/auth";
import { put } from "@/lib/api";

export default function Login() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { const s = getSession(); if (s) router.replace(s.role === "admin" ? "/admin" : "/student"); }, [router]);

  const submit = async (e?: React.FormEvent, em = email, pw = password) => {
    e?.preventDefault(); setError(""); setBusy(true);
    const user = login(em, pw);
    if (!user) { setError("Invalid email or password."); setBusy(false); return; }
    try {
      // make sure the demo students exist (with their real email addresses) in the backend
      await Promise.all(DEMO_USERS.filter((u) => u.role === "student").map((u) => put(`subscribers/${u.subscriberId}`, { email: u.email, name: u.name })));
    } catch { /* backend may be down; pages will show it */ }
    router.replace(user.role === "admin" ? "/admin" : "/student");
  };

  return (
    <div className="login">
      <div className="card">
        <h1>Sign in</h1>
        <p className="muted">The Herald campus notifications. Demo authentication (mock SSO).</p>
        <form onSubmit={submit}>
          <label>Email</label>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@campus.edu" autoFocus required />
          <label>Password</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password" required />
          {error && <p className="err">{error}</p>}
          <button style={{ width: "100%", marginTop: 16 }} disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </div>
      <div className="card" style={{ marginTop: 14 }}>
        <h2>Demo accounts</h2>
        {DEMO_USERS.map((u) => (
          <div className="toggle" key={u.email}>
            <div><b>{u.name}</b> <span className="pill">{u.role}</span><div className="muted small">{u.email} · {u.password}</div></div>
            <button className="secondary" onClick={() => submit(undefined, u.email, u.password)}>Use</button>
          </div>
        ))}
      </div>
    </div>
  );
}
