"use client";
export type Toast = { id: number; icon: string; title: string; body?: string };

export default function Toasts({ items }: { items: Toast[] }) {
  return (
    <div className="toasts">
      {items.map((t) => (
        <div key={t.id} className="toast">
          <div><b>{t.title}</b>{t.body && <div className="muted small">{t.body}</div>}</div>
        </div>
      ))}
    </div>
  );
}
