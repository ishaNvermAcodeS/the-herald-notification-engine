"use client";
import { useCallback, useEffect, useState } from "react";
import { api, put, post, Channels, InboxItem } from "@/lib/api";

type Inbox = { unreadCount: number; items: InboxItem[] };

export default function Student() {
  const [sid, setSid] = useState("alice");
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [prefs, setPrefs] = useState<Channels | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const saved = window.localStorage.getItem("herald.student");
    if (saved) setSid(saved);
  }, []);

  const load = useCallback(async () => {
    try {
      const [i, p] = await Promise.all([
        api<Inbox>(`notifications?subscriberId=${encodeURIComponent(sid)}`),
        api<{ channels: Channels }>(`subscribers/${encodeURIComponent(sid)}/preferences`),
      ]);
      setInbox(i); setPrefs(p.channels); setError("");
    } catch (e: any) {
      setInbox(null); setPrefs(null);
      setError(String(e.message).includes("does not exist") ? `No subscriber "${sid}" yet — an admin needs to send them an event first.` : e.message);
    }
  }, [sid]);

  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [load]);

  const toggle = async (k: keyof Channels) => {
    if (!prefs) return;
    setPrefs({ ...prefs, [k]: !prefs[k] });
    await put(`subscribers/${encodeURIComponent(sid)}/preferences`, { [k]: !prefs[k] });
    load();
  };
  const read = async (id: string) => { await post(`notifications/${id}/read?subscriberId=${encodeURIComponent(sid)}`, {}); load(); };

  return (
    <div className="stack" style={{ maxWidth: 640, margin: "0 auto" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Notifications {inbox ? <span className="pill PROCESSING">{inbox.unreadCount} unread</span> : null}</h1>
      </div>
      <div className="card">
        <label>Viewing as</label>
        <input value={sid} onChange={(e) => { setSid(e.target.value); window.localStorage.setItem("herald.student", e.target.value); }} />
      </div>
      {error && <div className="notice err">{error}</div>}

      {prefs && (
        <div className="card">
          <h2>Delivery preferences</h2>
          {(["email", "in_app"] as const).map((k) => (
            <div className="toggle" key={k}>
              <span>{k === "email" ? "Email" : "In-app"}</span>
              <button className={prefs[k] ? "" : "secondary"} onClick={() => toggle(k)}>{prefs[k] ? "On" : "Off"}</button>
            </div>
          ))}
          <p className="muted small">Emergency alerts always reach you, regardless of these settings.</p>
        </div>
      )}

      {inbox?.items.length === 0 && <div className="notice">Nothing yet.</div>}
      {inbox?.items.map((n) => (
        <div key={n.id} className={`item ${n.read ? "" : "unread"}`}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h3>{n.title}</h3>
            <span className="muted small">{new Date(n.createdAt).toLocaleString()}</span>
          </div>
          {n.aiSummary ? (
            <div className="ai">
              <b>{n.aiSummary.source === "ai" ? "✨ AI summary" : "Summary"}</b>
              <div>{n.aiSummary.tldr}</div>
              {n.aiSummary.action_items.length > 0 && (<ul>{n.aiSummary.action_items.map((a, i) => <li key={i}>{a}</li>)}</ul>)}
            </div>
          ) : (
            <p style={{ whiteSpace: "pre-wrap" }}>{n.body}</p>
          )}
          <div className="row" style={{ marginTop: 8 }}>
            <span className="muted small">{n.workflowId}</span>
            {!n.read && <button className="secondary" onClick={() => read(n.id)}>Mark read</button>}
          </div>
        </div>
      ))}
    </div>
  );
}
