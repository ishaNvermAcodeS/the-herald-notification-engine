"use client";
import { useEffect, useState } from "react";

const SAMPLES = [
  ["L", "Library closes at 6 PM", "Campus Bulletin"], ["T", "North shuttle delayed 20 min", "Transport"],
  ["E", "CS201 midterm moved to Hall 7", "Exams"], ["M", "Block A water outage 9-12", "Maintenance"],
  ["W", "Hostel Wi-Fi down tonight", "IT Services"], ["!", "Severe weather alert", "Emergency"],
  ["C", "Cafeteria closes at 8 PM", "Campus Life"], ["D", "3 new updates for you", "Digest"],
  ["@", "Email delivered", "The Herald"], ["S", "Summary ready", "Smart Digest"],
];

type Bit = { id: number; s: (typeof SAMPLES)[number]; left: number; delay: number; dur: number; scale: number; blur: number };

// Floating "notification toasts" drifting up behind the UI. Generated after mount so SSR/CSR markup match.
export default function Backdrop() {
  const [bits, setBits] = useState<Bit[]>([]);
  useEffect(() => {
    setBits(Array.from({ length: 16 }, (_, i) => ({
      id: i, s: SAMPLES[i % SAMPLES.length], left: Math.random() * 92, delay: -Math.random() * 40,
      dur: 28 + Math.random() * 26, scale: 0.8 + Math.random() * 0.5, blur: Math.random() < 0.5 ? 0 : 1.5,
    })));
  }, []);
  return (
    <div className="backdrop" aria-hidden>
      
      {bits.map((b) => (
        <div key={b.id} className="float" style={{ left: `${b.left}%`, animationDelay: `${b.delay}s`, animationDuration: `${b.dur}s`, transform: `scale(${b.scale})`, filter: `blur(${b.blur}px)` }}>
          <span className="fi">{b.s[0]}</span>
          <span><b>{b.s[2]}</b><br />{b.s[1]}</span>
        </div>
      ))}
    </div>
  );
}
