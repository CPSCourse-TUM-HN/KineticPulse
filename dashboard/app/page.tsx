"use client";

import { useEffect, useState } from "react";
import MonitoringDashboard from "./components/MonitoringDashboard";
import { fetchMonitoringSnapshot } from "../lib/monitoring/clientDataSource";
import type { MonitoringModel } from "../lib/monitoring/model";

const POLL_INTERVAL_MS = 3000;

export default function HomePage() {
  const [model, setModel] = useState<MonitoringModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const nextModel = await fetchMonitoringSnapshot();
        if (cancelled) return;
        setModel(nextModel);
        setError(null);
      } catch (reason) {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Monitoring backend unavailable");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <MonitoringDashboard
      model={model}
      loading={loading}
      error={error}
    />
  );
}
