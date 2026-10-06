import { NextRequest, NextResponse } from "next/server";

// Server-side proxy: the browser talks only to /api/herald/*; the Herald API key
// stays on the server and is never shipped to the client.
const API_URL = process.env.HERALD_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.HERALD_API_KEY ?? "herald-dev-api-key";

async function handler(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const url = `${API_URL}/v1/${path.join("/")}${req.nextUrl.search}`;
  const hasBody = !["GET", "HEAD"].includes(req.method);
  try {
    const upstream = await fetch(url, {
      method: req.method,
      headers: {
        Authorization: `Bearer ${API_KEY}`,
        "Content-Type": "application/json",
      },
      body: hasBody ? await req.text() : undefined,
      cache: "no-store",
    });
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "Herald API unreachable" }, { status: 502 });
  }
}

export { handler as GET, handler as POST, handler as PUT, handler as DELETE };
