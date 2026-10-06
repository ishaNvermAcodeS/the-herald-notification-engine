// MOCK authentication for the demo: credentials live in the client on purpose.
// It mimics an SSO/login flow; it is not real security.
export type Role = "admin" | "student";
export type User = { email: string; name: string; role: Role; subscriberId?: string };

export const DEMO_USERS: (User & { password: string })[] = [
  { email: "admin@herald.edu", password: "admin123", name: "Campus Admin", role: "admin" },
  { email: "iamvermaishan@gmail.com", password: "student123", name: "Ishan", role: "student", subscriberId: "ishan" },
  { email: "akshat1feb@gmail.com", password: "student123", name: "Akshat", role: "student", subscriberId: "akshat" },
];

const KEY = "herald.session";
export const getSession = (): User | null => {
  try { const v = window.localStorage.getItem(KEY); return v ? (JSON.parse(v) as User) : null; } catch { return null; }
};
export const login = (email: string, password: string): User | null => {
  const u = DEMO_USERS.find((x) => x.email.toLowerCase() === email.trim().toLowerCase() && x.password === password);
  if (!u) return null;
  const { password: _p, ...user } = u;
  window.localStorage.setItem(KEY, JSON.stringify(user));
  return user;
};
export const logout = () => window.localStorage.removeItem(KEY);
