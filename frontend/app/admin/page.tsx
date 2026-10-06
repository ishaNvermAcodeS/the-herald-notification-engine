"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, post, put, AdminNotification, Channels, Digest, Job } from "@/lib/api";
import Toasts, { Toast } from "@/components/Toasts";
import { useAuth } from "@/components/useAuth";

type Overview = {
  totalEvents: number; totalNotifications: number; delivered: { email: number; inApp: number };
  failed: number; pending: number; retrying: number; skipped: number; digestCount: number; digestWindowSeconds: number;
};
type Sub = { subscriberId: string; email: string | null; name: string | null; channels: Channels };

const CATEGORIES = ["announcement", "maintenance", "exam", "transport", "weather", "event"];

function friendlyError(err: string | null): string | null {
  if (!err) return null;
  const m = err.match(/testing emails to your own email address \(([^)]+)\)/);
  if (m) return `Resend test mode: only ${m[1]} can receive email. Verify a domain at resend.com/domains to email anyone else.`;
  if (err.includes("EMAIL_API_KEY")) return "No email API key configured.";
  return err.length > 140 ? err.slice(0, 140) + "…" : err;
}

function stepView(job: Job, noteStatus: string, now: number): { tone: string; text: string; sub?: string } {
  const left = job.delayUntil ? Math.max(0, Math.round((new Date(job.delayUntil).getTime() - now) / 1000)) : 0;
  if (job.stepType === "DIGEST") {
    if (job.status === "DELAYED") return { tone: "warn", text: "Collecting events", sub: left > 0 ? `window closes in ${left}s` : "closing…" };
    if (job.status === "MERGED") return { tone: "info", text: "Merged into digest" };
    if (job.status === "COMPLETED") return { tone: "ok", text: "Digest ready" };
  }
  switch (job.status) {
    case "COMPLETED": return { tone: "ok", text: "Delivered " };
    case "SKIPPED": return { tone: "", text: job.skipReason === "SUBSCRIBER_PREFERENCE" ? "Muted by subscriber" : "No email address on file" };
    case "DELAYED": return { tone: "warn", text: `Retrying (attempt ${job.attempts}/${job.maxAttempts})`, sub: friendlyError(job.error) ?? undefined };
    case "FAILED": return { tone: "bad", text: "Failed", sub: friendlyError(job.error) ?? undefined };
    case "RUNNING": return { tone: "info", text: "Sending…" };
    case "MERGED": return { tone: "info", text: "Merged into digest" };
    default: return { tone: "warn", text: noteStatus === "PROCESSING" ? "Waiting for digest" : "Queued" };
  }
}
const label: Record<string, string> = { DIGEST: "Digest", EMAIL: "Email", IN_APP: "In-app" };

export default function Admin() {
  const user = useAuth("admin");
  const [ov, setOv] = useState<Overview | null>(null);
  const [notes, setNotes] = useState<AdminNotification[]>([]);
  const [digests, setDigests] = useState<Digest[]>([]);
  const [subs, setSubs] = useState<Sub[]>([]);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const [toasts, setToasts] = useState<Toast[]>([]);

  const [workflow, setWorkflow] = useState("campus-bulletin");
  const [recipients, setRecipients] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [title, setTitle] = useState("Library closing early");
  const [message, setMessage] = useState("The library will close at 6 PM today for maintenance.");
  const [category, setCategory] = useState("maintenance");
  const [busy, setBusy] = useState(false);
  const [tracked, setTracked] = useState<string[]>([]);
  const [subForm, setSubForm] = useState({ id: "", email: "" });
  const seen = useRef<Set<string>>(new Set());
  const toastId = useRef(0);

  const toast = useCallback((icon: string, title: string, body?: string) => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, icon, title, body }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5200);
  }, []);

  const load = useCallback(async () => {
    try {
      const [o, n, d, s] = await Promise.all([
        api<Overview>("admin/overview"), api<AdminNotification[]>("admin/notifications?limit=60"),
        api<Digest[]>("admin/digests"), api<Sub[]>("subscribers"),
      ]);
      setOv(o); setNotes(n); setDigests(d); setSubs(s); setError("");
    } catch (e: any) { setError(e.message); }
  }, []);
  useEffect(() => { if (!user) return; load(); const t = setInterval(load, 2500); return () => clearInterval(t); }, [load, user]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);

  // toast when a tracked delivery completes
  useEffect(() => {
    for (const n of notes) {
      if (!tracked.some((tx) => n.transactionId.startsWith(tx + ":"))) continue;
      for (const j of n.jobs) {
        if (j.stepType === "DIGEST" || j.status !== "COMPLETED" || seen.current.has(j.id)) continue;
        seen.current.add(j.id);
        toast(j.stepType === "EMAIL" ? "" : "", `${j.stepType === "EMAIL" ? "Email" : "In-app"} delivered`, `to ${n.subscriberId}`);
      }
    }
  }, [notes, tracked, toast]);

  const addRecipient = (v: string) => {
    const id = v.trim().replace(/,$/, "");
    if (id && !recipients.includes(id)) setRecipients([...recipients, id]);
    setDraft("");
  };

  const send = async (count: number) => {
    const to = draft.trim() ? [...recipients, draft.trim()] : recipients;
    setBusy(true);
    try {
      const events = Array.from({ length: count }, (_, i) => ({
        workflowId: workflow, to: to.length === 1 ? to[0] : to,
        payload: { title: count > 1 ? `${title} (${i + 1}/${count})` : title, message, category },
      }));
      const r: any = count === 1 ? await post("events/trigger", events[0]) : await post("events/trigger-bulk", { events });
      const txs: string[] = count === 1 ? [r.transactionId] : r.transactionIds;
      setTracked(txs); setRecipients(to); setDraft("");
      toast("", "Accepted", `${count} event${count > 1 ? "s" : ""} queued for ${to.length} recipient${to.length > 1 ? "s" : ""}`);
      setTimeout(load, 700);
    } catch (e: any) { toast("", "Couldn't send", e.message); }
    setBusy(false);
  };

  const saveSub = async () => {
    if (!subForm.id.trim()) return;
    await put(`subscribers/${encodeURIComponent(subForm.id.trim())}`, { email: subForm.email.trim() || null });
    toast("", "Saved", subForm.id); setSubForm({ id: "", email: "" }); load();
  };

  // tracker data
  const trackedNotes = notes.filter((n) => tracked.some((tx) => n.transactionId.startsWith(tx + ":")));
  const bySub = new Map<string, AdminNotification[]>();
  trackedNotes.forEach((n) => bySub.set(n.subscriberId, [...(bySub.get(n.subscriberId) ?? []), n]));
  const win = ov?.digestWindowSeconds ?? 300;
  const winText = win >= 60 ? `${Math.round(win / 60)} min` : `${win}s`;
  const WORKFLOWS = [
    { id: "campus-maintenance-alert", icon: "", name: "Instant alert", desc: "Email + in-app right away" },
    { id: "campus-bulletin", icon: "", name: "Digest bulletin", desc: `Grouped per person, delivered after ${winText}` },
    { id: "campus-emergency", icon: "", name: "Emergency", desc: "Ignores mute settings" },
  ];
  const known = subs.map((s) => s.subscriberId).filter((id) => !recipients.includes(id));
  const emailsMissing = recipients.filter((r) => { const s = subs.find((x) => x.subscriberId === r); return s && !s.email; });

  if (!user) return null;
  return (
    <div className="stack">
      <Toasts items={toasts} />
      <div><h1>Admin <span className="grad">control room</span></h1><div className="muted">Send a notification and watch it travel through the pipeline.</div></div>
      {error && <div className="notice warn">Can't reach the backend: {error}</div>}

      {ov && (
        <div className="grid4">
          {([["", "Events", ov.totalEvents], ["", "Notifications", ov.totalNotifications], ["", "Emails sent", ov.delivered.email], ["", "In-app delivered", ov.delivered.inApp],
            ["", "Failed", ov.failed], ["", "Waiting / queued", ov.pending], ["", "Retrying", ov.retrying], ["", "Digest windows", ov.digestCount]] as [string, string, number][]).map(([i, k, v]) => (
            <div className="metric" key={k}><b>{v}</b><span>{i} {k}</span></div>
          ))}
        </div>
      )}

      <div className="grid2">
        <div className="card">
          <h2>Compose notification</h2>
          <label>Type</label>
          <div className="opts">{WORKFLOWS.map((w) => (
            <button key={w.id} className={`opt ${workflow === w.id ? "on" : ""}`} onClick={() => setWorkflow(w.id)}><b>{w.icon} {w.name}</b><span>{w.desc}</span></button>))}</div>

          <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end" }}><label style={{ marginBottom: 0 }}>Send to</label><button className="secondary" style={{ padding: "4px 12px", fontSize: 12 }} disabled={!subs.length} onClick={() => setRecipients(subs.map((x) => x.subscriberId))}>Add all ({subs.length})</button></div>
          <div style={{ height: 6 }} />
          <div className="chips">
            {recipients.map((r) => <span className="chip" key={r}>{r}<button onClick={() => setRecipients(recipients.filter((x) => x !== r))}>×</button></span>)}
            <input value={draft} placeholder={recipients.length ? "add another…" : "type a subscriber id and press Enter, or use Add all"}
              onChange={(e) => (e.target.value.endsWith(",") || e.target.value.endsWith(" ")) ? addRecipient(e.target.value) : setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addRecipient(draft); } if (e.key === "Backspace" && !draft && recipients.length) setRecipients(recipients.slice(0, -1)); }} />
          </div>
          {known.length > 0 && <div className="row" style={{ marginTop: 6 }}>{known.map((id) => <button key={id} className="secondary" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => setRecipients([...recipients, id])}>+ {id}</button>)}</div>}
          {emailsMissing.length > 0 && <p className="notice warn small" style={{ marginTop: 8 }}>{emailsMissing.join(", ")} {emailsMissing.length > 1 ? "have" : "has"} no email address — only in-app will be delivered. Add one under Subscribers.</p>}

          <label>Title</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Library closing early" />
          <label>Message</label>
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Write the notification text…" />
          <label>Category</label>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>{CATEGORIES.map((c) => <option key={c}>{c}</option>)}</select>

          <div className="row" style={{ marginTop: 16 }}>
            <button disabled={busy || !title.trim() || (!recipients.length && !draft.trim())} onClick={() => send(1)}>{busy ? "Sending…" : "Send notification"}</button>
            <button className="secondary" disabled={busy || !title.trim() || (!recipients.length && !draft.trim())} onClick={() => send(5)}>Send 5 updates (digest demo)</button>
          </div>
        </div>

        <div className="stack">
          <div className="card">
            <h2>Live delivery tracker</h2>
            {bySub.size === 0 && <p className="muted">Send a notification to see each step here, live.</p>}
            {[...bySub.entries()].map(([sid, list]) => {
              const master = list.find((n) => n.status !== "MERGED") ?? list[0];
              const merged = list.filter((n) => n.status === "MERGED").length;
              return (
                <div className="track" key={sid} style={{ marginBottom: 10 }}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <b>{sid}</b>
                    <span className="muted small">{list.length > 1 ? `${list.length} updates · ${merged} merged into one digest` : master.payload.title}</span>
                  </div>
                  <div className="steps">
                    {master.jobs.map((j) => { const v = stepView(j, master.status, now);
                      return (<div className="step" key={j.id}><span>{label[j.stepType]}</span><span className={`pill ${v.tone}`}>{v.tone === "warn" && <i className="pulse" />}{v.text}</span>{v.sub && <small className={v.tone === "bad" ? "err" : ""}>{v.sub}</small>}</div>); })}
                  </div>
                </div>);
            })}
          </div>

          <div className="card">
            <h2>Subscribers</h2>
            <p className="muted small" style={{ marginTop: -6 }}>Channel preferences are set by each student in their own dashboard.</p>
            <div className="row">
              <input style={{ flex: 1, minWidth: 90 }} placeholder="subscriber id" value={subForm.id} onChange={(e) => setSubForm({ ...subForm, id: e.target.value })} />
              <input style={{ flex: 2, minWidth: 160 }} placeholder="email address" value={subForm.email} onChange={(e) => setSubForm({ ...subForm, email: e.target.value })} />
              <button onClick={saveSub}>Save</button>
            </div>
            <p className="notice warn small" style={{ marginTop: 10 }}>Email goes through Resend's test sender: until a domain is verified, only the Resend account owner's address can receive real email. Other addresses will show "Failed" with the reason.</p>
            <div className="scroll"><table><thead><tr><th>ID</th><th>Email address</th><th>Email pref</th><th>In-app pref</th></tr></thead><tbody>
              {subs.map((s) => (
                <tr key={s.subscriberId}><td><b>{s.subscriberId}</b></td><td>{s.email ?? <span className="muted">— none —</span>}</td>
                  <td><span className={`pill ${s.channels.email ? "ok" : ""}`}>{s.channels.email ? "on" : "off"}</span></td>
                  <td><span className={`pill ${s.channels.in_app ? "ok" : ""}`}>{s.channels.in_app ? "on" : "off"}</span></td></tr>
              ))}
            </tbody></table></div>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Digest windows &amp; AI summaries</h2>
        <div className="scroll"><table><thead><tr><th>Subscriber</th><th>Status</th><th>Events</th><th>Closes</th><th>Summary</th></tr></thead><tbody>
          {digests.length === 0 && <tr><td colSpan={5} className="muted">No digests yet — send a “Digest bulletin”.</td></tr>}
          {digests.map((d) => (
            <tr key={d.id}><td><b>{d.subscriberId}</b><div className="muted small">{d.workflowId}</div></td>
              <td><span className={`pill ${d.status === "DISPATCHED" ? "ok" : "warn"}`}>{d.status === "OPEN" ? "collecting" : d.status === "DISPATCHED" ? "sent" : d.status.toLowerCase()}</span></td>
              <td>{d.eventCount}</td><td>{new Date(d.windowEnd).toLocaleTimeString()}</td>
              <td>{d.summary ? (<div><span className={`pill ${d.summary.source === "ai" ? "ok" : ""}`}>{d.summary.source === "ai" ? "AI" : "plain"}</span> {d.summary.tldr}
                {d.summary.action_items.map((a, i) => <div key={i} className="small">• {a}</div>)}
                {d.summary.ai_error && <div className="small muted">AI unavailable: {d.summary.ai_error}</div>}</div>) : <span className="muted">waiting for window…</span>}</td></tr>
          ))}
        </tbody></table></div>
      </div>

      <div className="card">
        <h2>All notifications &amp; jobs</h2>
        <div className="scroll"><table><thead><tr><th>Subscriber</th><th>Event</th><th>Status</th><th>Steps</th></tr></thead><tbody>
          {notes.map((n) => (
            <tr key={n.id}><td><b>{n.subscriberId}</b><div className="muted small">{new Date(n.createdAt).toLocaleTimeString()}</div></td>
              <td>{n.payload.title ?? n.workflowId}<div className="muted small">{n.workflowId}</div></td>
              <td><span className={`pill ${n.status === "COMPLETED" ? "ok" : n.status === "FAILED" ? "bad" : n.status === "MERGED" ? "info" : "warn"}`}>{n.status.toLowerCase()}</span></td>
              <td>{n.jobs.map((j) => { const v = stepView(j, n.status, now); return <div key={j.id} className="small">{label[j.stepType]} <span className={`pill ${v.tone}`}>{v.text}</span>{v.sub && <span className={v.tone === "bad" ? "err" : "muted"}> {v.sub}</span>}</div>; })}</td></tr>
          ))}
        </tbody></table></div>
      </div>
    </div>
  );
}
