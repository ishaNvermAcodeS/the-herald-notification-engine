export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/herald/${path}`, { ...init, cache: "no-store" });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const msg = data?.detail ?? data?.message ?? res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data as T;
}

export const post = (path: string, body: unknown) =>
  api(path, { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
export const put = (path: string, body: unknown) =>
  api(path, { method: "PUT", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });

export type Channels = { email: boolean; in_app: boolean };
export type Job = {
  id: string; stepType: string; status: string; skipReason: string | null;
  attempts: number; maxAttempts: number; delayUntil: string | null; error: string | null;
};
export type AdminNotification = {
  id: string; transactionId: string; subscriberId: string; workflowId: string; status: string;
  payload: Record<string, any>; digestWindowId: string | null; createdAt: string;
  jobs: Job[]; messages: { id: string; channel: string; status: string; providerMessageId: string | null }[];
};
export type Digest = {
  id: string; subscriberId: string; workflowId: string; status: string; eventCount: number;
  windowStart: string; windowEnd: string;
  summary: { source: string; tldr: string; action_items: string[]; ai_error?: string } | null;
  events: { transactionId: string; payload: Record<string, any> }[];
};
export type InboxItem = {
  id: string; workflowId: string; title: string | null; body: string; read: boolean;
  status: string; createdAt: string; aiSummary: Digest["summary"];
};

