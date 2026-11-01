import { useEffect, useState } from "react";

type ApiStatus = "checking" | "ok" | "down";

const STATUS_COLOR: Record<ApiStatus, string> = {
  checking: "text-slate-400",
  ok: "text-emerald-600",
  down: "text-rose-600",
};

export default function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");

  useEffect(() => {
    fetch("/api/health")
      .then((res) => setApiStatus(res.ok ? "ok" : "down"))
      .catch(() => setApiStatus("down"));
  }, []);

  return (
    <main className="min-h-screen bg-slate-50 px-8 py-10 text-slate-900">
      <h1 className="text-2xl font-semibold tracking-tight">Converge</h1>
      <p className="mt-1 text-sm text-slate-500">Git for your lab's research ideas</p>
      <p className="mt-8 text-sm">
        API <span className={STATUS_COLOR[apiStatus]}>{apiStatus}</span>
      </p>
    </main>
  );
}
