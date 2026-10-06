"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSession, Role, User } from "@/lib/auth";

// Returns the signed-in user (or null while checking); redirects to /login when missing or wrong role.
export function useAuth(role?: Role): User | null {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  useEffect(() => {
    const s = getSession();
    if (!s) { router.replace("/login"); return; }
    if (role && s.role !== role) { router.replace(s.role === "admin" ? "/admin" : "/student"); return; }
    setUser(s);
  }, [role, router]);
  return user;
}
