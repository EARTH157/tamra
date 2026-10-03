import { useEffect, useState } from "react";
import { api } from "./api";

type Health = { status: string; version: string };

export default function App() {
  const [message, setMessage] = useState("Connecting to Tamra core…");

  useEffect(() => {
    api<Health>("GET", "/api/health")
      .then((h) => setMessage(`Tamra core: ${h.status} (v${h.version})`))
      .catch((e: Error) => setMessage(`Tamra core unreachable: ${e.message}`));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 24 }}>
      <h1>Tamra</h1>
      <p>{message}</p>
    </main>
  );
}
