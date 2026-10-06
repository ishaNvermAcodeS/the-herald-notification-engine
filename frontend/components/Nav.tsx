"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getSession, logout, User } from "@/lib/auth";

export default function Nav() {
  const path = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  useEffect(() => { setUser(getSession()); }, [path]);
  return (
    <header className="nav">
      <Link href="/" className="brand"><span className="logo">H</span> The Herald</Link>
      <nav>
        {user && <span className="who">{user.name} · {user.role}</span>}
        {user && <button className="secondary" onClick={() => { logout(); setUser(null); router.replace("/login"); }}>Log out</button>}
      </nav>
    </header>
  );
}
