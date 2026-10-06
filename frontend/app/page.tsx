import Link from "next/link";

export default function Home() {
  return (
    <div className="stack">
      <h1>The Herald</h1>
      <p className="muted">
        A clean-room campus notification engine: events → workflow → preferences → digest → delivery (email + in-app) → retry,
        with AI-synthesised digests.
      </p>
      <div className="grid2">
        <Link className="card link" href="/admin"><h2>Admin</h2><p className="muted">Dispatch events, watch jobs, retries and digests.</p></Link>
        <Link className="card link" href="/student"><h2>Student</h2><p className="muted">Notification feed, AI digest and channel preferences.</p></Link>
      </div>
    </div>
  );
}
