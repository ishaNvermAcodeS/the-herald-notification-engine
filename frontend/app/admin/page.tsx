"use client";
import { useCallback, useEffect, useState } from "react";
import { api, post, put, AdminNotification, Channels, Digest } from "@/lib/api";

type Overview = {
  totalEvents: number; totalNotifications: number; delivered: { email: number; inApp: number };
  failed: number; pending: number; retrying: number; skipped: number; digestCount: number;
};
type Sub = { subscriberId: string; email: string | null; name: string | null; channels: Channels };

const WORKFLOWS = ["campus-bulletin", "campus-maintenance-alert", "campus-emergency"];
const pill = (s: string) => <span className={`pill ${s}`}>{s}</span>;

export default function Admin() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [notes, setNotes] = useState<AdminNotification[]>([]);
  const [digests, setDigests] = useState<Digest[]>([]);
  const [subs, setSubs] = useState<Sub[]>([]);
  const [error, setError] = useState("");

  const [workflow, setWorkflow] = useState("campus-bulletin");
  const [to, setTo] = useState("alice, bob");
  const [payload, setPayload] = useState(JSON.stringify({ title: "Library Maintenance", message: "Library will close at 6 PM today.", category: "maintenance" }, null, 2));
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const [subForm, setSubForm] = useState({ id: "alice", email: "" });

  const load = useCallback(async () => {
    try {
      const [o, n, d, s] = await Promise.all([
        api<Overview>("admin/overview"), api<AdminNotification[]>("admin/notifications?limit=40"),
        api<Digest[]>("admin/digests"), api<Sub[]>("subscribers"),
      ]);
      setOv(o); setNotes(n); setDigests(d); setSubs(s); setError("");
    } catch (e: any) { setError(e.message); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);

  const recipients = () => to.split(",").map((x) => x.trim()).filter(Boolean);
  const send = async (count: number) => {
    setBusy(true); setResult("");
    try {
      const body = JSON.parse(payload);
      const events = Array.from({ length: count }, (_, i) => ({
        workflowId: workflow, to: recipients().length === 1 ? recipients()[0] : recipients(),
        payload: count > 1 ? { ...body, title: `${body.title} (${i + 1}/${count})` } : body,
      }));
      const r: any = count === 1 ? await post("events/trigger", events[0]) : await post("events/trigger-bulk", { events });
      setResult(count === 1
        ? `Accepted (${r.status}) · transactionId ${r.transactionId} — the worker is processing it.`
        : `Accepted ${r.count} events — the worker is processing them.`);
      setTimeout(load, 800);
    } catch (e: any) { setResult(`Error: ${e.message}`); }
    setBusy(false);
  };
  const saveSub = async () => {
    await put(`subscribers/${encodeURIComponent(subForm.id)}`, { email: subForm.email || null });
    load();
  };
  const togglePref = async (s: Sub, k: keyof Channels) => { await put(`subscribers/${s.subscriberId}/preferences`, { [k]: !s.channels[k] }); load(); };

  return (
    <div className="stack">
      <h1>Admin</h1>
      {error && <div className="notice err">Backend error: {error}</div>}

      {ov && (
        <div className="grid3">
          {([["Events", ov.totalEvents], ["Notifications", ov.totalNotifications], ["Emails sent", ov.delivered.email],
            ["In-app delivered", ov.delivered.inApp], ["Failed", ov.failed], ["Pending", ov.pending],
            ["Retrying", ov.retrying], ["Digest windows", ov.digestCount]] as [string, number][]).map(([k, v]) => (
            <div className="metric" key={k}><b>{v}</b><span>{k}</span></div>
          ))}
        </div>
      )}

      <div className="grid2">
        <div className="card">
          <h2>Dispatch a campus event</h2>
          <label>Workflow</label>
          <select value={workflow} onChange={(e) => setWorkflow(e.target.value)}>{WORKFLOWS.map((w) => <option key={w}>{w}</option>)}</select>
          <label>Recipients (comma separated subscriber IDs)</label>
          <input value={to} onChange={(e) => setTo(e.target.value)} />
          <label>Payload (JSON)</label>
          <textarea value={payload} onChange={(e) => setPayload(e.target.value)} />
          <div className="row" style={{ marginTop: 12 }}>
            <button disabled={busy} onClick={() => send(1)}>Send Notification</button>
            <button className="secondary" disabled={busy} onClick={() => send(5)}>Send burst ×5</button>
          </div>
          {result && <p className="notice">{result}</p>}
          <p className="muted small">campus-bulletin digests events per subscriber (window set by the worker); the others deliver immediately.</p>
        </div>

        <div className="card">
          <h2>Subscribers</h2>
          <div className="row">
            <input style={{ flex: 1, minWidth: 90 }} placeholder="subscriber id" value={subForm.id} onChange={(e) => setSubForm({ ...subForm, id: e.target.value })} />
            <input style={{ flex: 2, minWidth: 160 }} placeholder="email address (for real email)" value={subForm.email} onChange={(e) => setSubForm({ ...subForm, email: e.target.value })} />
            <button onClick={saveSub}>Save</button>
          </div>
          <div className="scroll"><table><thead><tr><th>ID</th><th>Email</th><th>Email pref</th><th>In-app pref</th></tr></thead><tbody>
            {subs.map((s) => (
              <tr key={s.subscriberId}><td>{s.subscriberId}</td><td>{s.email ?? <span className="muted">—</span>}</td>
                <td><button className="secondary" onClick={() => togglePref(s, "email")}>{s.channels.email ? "on" : "off"}</button></td>
                <td><button className="secondary" onClick={() => togglePref(s, "in_app")}>{s.channels.in_app ? "on" : "off"}</button></td></tr>
            ))}
          </tbody></table></div>
        </div>
      </div>

      <div className="card">
        <h2>Digest windows</h2>
        <div className="scroll"><table><thead><tr><th>Subscriber</th><th>Status</th><th>Events</th><th>Window ends</th><th>Summary</th></tr></thead><tbody>
          {digests.map((d) => (
            <tr key={d.id}><td>{d.subscriberId}<div className="muted small">{d.workflowId}</div></td><td>{pill(d.status)}</td><td>{d.eventCount}</td>
              <td>{new Date(d.windowEnd).toLocaleTimeString()}</td>
              <td>{d.summary ? (<div><span className="pill COMPLETED">{d.summary.source}</span> {d.summary.tldr}
                {d.summary.action_items.map((a, i) => <div key={i} className="small">• {a}</div>)}
                {d.summary.ai_error && <div className="small muted">AI unavailable: {d.summary.ai_error}</div>}</div>) : <span className="muted">pending</span>}</td></tr>
          ))}
        </tbody></table></div>
      </div>

      <div className="card">
        <h2>Notifications, jobs &amp; delivery</h2>
        <div className="scroll"><table><thead><tr><th>Subscriber</th><th>Event</th><th>Status</th><th>Jobs (attempts)</th><th>Messages</th></tr></thead><tbody>
          {notes.map((n) => (
            <tr key={n.id}>
              <td>{n.subscriberId}<div className="muted small">{new Date(n.createdAt).toLocaleTimeString()}</div></td>
              <td>{n.payload.title ?? n.workflowId}<div className="muted small">{n.workflowId}</div></td>
              <td>{pill(n.status)}</td>
              <td>{n.jobs.map((j) => (
                <div key={j.id} className="small">{j.stepType} {pill(j.status)} {j.attempts > 0 && `${j.attempts}/${j.maxAttempts}`}
                  {j.skipReason && <span className="muted"> {j.skipReason}</span>}
                  {j.status === "DELAYED" && j.delayUntil && j.stepType !== "DIGEST" && <span className="muted"> retry {new Date(j.delayUntil).toLocaleTimeString()}</span>}
                  {j.error && <div className="err">{j.error}</div>}</div>))}</td>
              <td>{n.messages.map((m) => <div key={m.id} className="small">{m.channel} {pill(m.status)}</div>)}</td>
            </tr>
          ))}
        </tbody></table></div>
      </div>
    </div>
  );
}
