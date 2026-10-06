"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getSession } from "@/lib/auth";

export default function Home() {
  const router = useRouter();
  useEffect(() => { const s = getSession(); router.replace(!s ? "/login" : s.role === "admin" ? "/admin" : "/student"); }, [router]);
  return <p className="muted">Loading…</p>;
}
