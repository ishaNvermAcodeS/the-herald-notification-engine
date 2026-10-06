"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, put, post, Channels, InboxItem } from "@/lib/api";
import Toasts, { Toast } from "@/components/Toasts";
import { useAuth } from "@/components/useAuth";

type Inbox = { unreadCount: number; items: InboxItem[] };

const ago = (iso: string) => {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : new Date(iso).toLocaleDateString();
};
const icon = (w: string) => (w.includes("emergency") ? "!" : w.includes("bulletin") ? "B" : "M");

export default function Student() {
  const user = useAuth("student");
  const sid = user?.subscriberId ?? "";
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [prefs, setPrefs] = useState<Channels | null>(null);
  const [error, setError] = useState("");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [ring, setRing] = useState(false);
  const known = useRef<Set<string> | null>(null);
  const tid = useRef(0);

  useEffect(() => { known.current = null; }, [sid]);

  const load = useCallback(async () => {
    if (!sid) return;
    try {
      const [i, p] = await Promise.all([
        api<Inbox>(`notifications?subscriberId=${encodeURIComponent(sid)}`),
        api<{ channels: Channels }>(`subscribers/${encodeURIComponent(sid)}/preferences`),
      ]);
      if (known.current) {
        const fresh = i.items.filter((x) => !known.current!.has(x.id));
        if (fresh.length) {
          setRing(true); setTimeout(() => setRing(false), 900);
          fresh.slice(0, 3).forEach((f) => {
            const id = ++tid.current;
            setToasts((t) => [...t, { id, icon: icon(f.workflowId), title: f.title ?? "New notification", body: f.aiSummary?.tldr ?? f.body.slice(0, 80) }]);
            setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5200);
          });
        }
      }
      known.current = new Set(i.items.map((x) => x.id));
      setInbox(i); setPrefs(p.channels); setError("");
    } catch (e: any) {
      setInbox(null); setPrefs(null);
      setError(String(e.message).includes("does not exist") ? `Your account is not set up yet. Sign out and sign in again.` : e.message);
    }
  }, [sid]);

  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);

  const toggle = async (k: keyof Channels) => {
    if (!prefs) return;
    setPrefs({ ...prefs, [k]: !prefs[k] });
    await put(`subscribers/${encodeURIComponent(sid)}/preferences`, { [k]: !prefs[k] });
    load();
  };
  const read = async (id: string) => { await post(`notifications/${id}/read?subscriberId=${encodeURIComponent(sid)}`, {}); load(); };
  const readAll = async () => { await Promise.all((inbox?.items ?? []).filter((n) => !n.read).map((n) => post(`notifications/${n.id}/read?subscriberId=${encodeURIComponent(sid)}`, {}))); load(); };

  if (!user) return null;
  return (
    <div className="stack phone">
      <Toasts items={toasts} />
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h1>Hi, <span className="grad">{user?.name}</span></h1>
          <div className="muted">{inbox ? (inbox.unreadCount ? `You have ${inbox.unreadCount} unread` : "You're all caught up") : "…"}</div></div>
        <div className={`bell ${ring ? "ring" : ""}`}>Inbox{inbox && inbox.unreadCount > 0 && <i>{inbox.unreadCount}</i>}</div>
      </div>

      {error && <div className="notice warn">{error}</div>}

      {prefs && (
        <div className="card">
          <h2>How should we reach you?</h2>
          {(["email", "in_app"] as const).map((k) => (
            <div className="toggle" key={k}>
              <div><b>{k === "email" ? "Email" : "In-app"}</b><div className="muted small">{k === "email" ? "Digests and alerts to your inbox" : "Shown here in your feed"}</div></div>
              <button className={`sw ${prefs[k] ? "on" : ""}`} onClick={() => toggle(k)} aria-label={`toggle ${k}`} />
            </div>
          ))}
          <p className="muted small" style={{ marginBottom: 0 }}>Emergency alerts always reach you.</p>
        </div>
      )}

      {inbox && inbox.items.length > 0 && inbox.unreadCount > 0 && (
        <div className="row" style={{ justifyContent: "space-between" }}><h2 style={{ margin: 0 }}>Notifications</h2><button className="secondary" onClick={readAll}>Mark all read</button></div>
      )}
      {inbox?.items.length === 0 && <div className="card" style={{ textAlign: "center" }}><div style={{ fontSize: 40 }}></div><div className="muted">Nothing yet — new notifications will pop up here.</div></div>}
      {inbox?.items.map((n) => (
        <div key={n.id} className={`item ${n.read ? "" : "unread"}`}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
            <div className="row" style={{ flexWrap: "nowrap" }}><span className="fi">{icon(n.workflowId)}</span><h3>{n.title}</h3></div>
            <span className="muted small">{ago(n.createdAt)}</span>
          </div>
          {n.aiSummary?.source === "ai" ? (
            <div className="ai">
              <b>AI summary</b>
              <div style={{ marginTop: 4 }}>{n.aiSummary.tldr}</div>
              {n.aiSummary.action_items.length > 0 && (<><div style={{ marginTop: 8 }}><b>What to do</b></div><ul>{n.aiSummary.action_items.map((a, i) => <li key={i}>{a}</li>)}</ul></>)}
            </div>
          ) : (<p style={{ whiteSpace: "pre-wrap", margin: "8px 0 0" }}>{n.body}</p>)}
          {!n.read && <div style={{ marginTop: 10 }}><button className="secondary" onClick={() => read(n.id)}>Mark as read</button></div>}
        </div>
      ))}
    </div>
  );
}
