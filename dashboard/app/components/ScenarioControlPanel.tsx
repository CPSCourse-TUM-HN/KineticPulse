"use client";

import { AlertTriangle, Play, RotateCcw, Square } from "lucide-react";
import Link from "next/link";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchControlState,
  resetScenario,
  restartScenario,
  selectScenario
} from "../../lib/control/clientControl";
import type { ControlModel, ScenarioOption } from "../../lib/control/model";
import { fetchMonitoringSnapshot } from "../../lib/monitoring/clientDataSource";
import type { MonitoringModel } from "../../lib/monitoring/model";

const POLL_INTERVAL_MS = 1000;
/** How long a Tier-2 button stays armed before it disarms itself. */
const ARM_TIMEOUT_MS = 5000;

const GROUP_LABELS: Record<ScenarioOption["group"], string> = {
  baseline: "Baseline",
  scenario: "PRD scenarios",
  playbook: "Scripted playbooks"
};

const GROUP_ORDER: Array<ScenarioOption["group"]> = ["baseline", "scenario", "playbook"];

function tierLabel(tier: string): string {
  const labels: Record<string, string> = {
    none: "No escalation",
    tier_1_verify: "Tier 1 — verify",
    tier_2_seizure: "Tier 2 — seizure",
    tier_2_cardiac: "Tier 2 — cardiac"
  };
  return labels[tier] ?? tier;
}

/** Tier 2 bypasses voice verification, so it dispatches without asking. */
function isHighImpact(option: ScenarioOption): boolean {
  return option.expectedTier.startsWith("tier_2");
}

export default function ScenarioControlPanel() {
  const [control, setControl] = useState<ControlModel | null>(null);
  const [monitoring, setMonitoring] = useState<MonitoringModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const armTimer = useRef<number | null>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    const [next, snapshot] = await Promise.allSettled([
      fetchControlState(signal),
      fetchMonitoringSnapshot(signal)
    ]);
    if (next.status === "fulfilled") {
      setControl(next.value);
      setError(null);
    } else if (!signal?.aborted) {
      setError(next.reason instanceof Error ? next.reason.message : "Control backend unavailable");
    }
    // The live readout is a convenience; losing it must not blank the panel.
    if (snapshot.status === "fulfilled") setMonitoring(snapshot.value);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const timer = window.setInterval(() => void refresh(controller.signal), POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refresh]);

  useEffect(() => () => {
    if (armTimer.current !== null) window.clearTimeout(armTimer.current);
  }, []);

  const disarm = useCallback(() => {
    if (armTimer.current !== null) window.clearTimeout(armTimer.current);
    armTimer.current = null;
    setArmed(null);
  }, []);

  const run = useCallback(
    async (key: string, action: () => Promise<ControlModel>) => {
      setBusy(key);
      try {
        setControl(await action());
        setError(null);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Control request failed");
      } finally {
        setBusy(null);
        disarm();
      }
    },
    [disarm]
  );

  const activate = useCallback(
    (option: ScenarioOption) => {
      // Two-step for Tier 2: those scenarios bypass voice verification and
      // dispatch straight away, so a stray click would really page someone.
      if (isHighImpact(option) && armed !== option.alias) {
        if (armTimer.current !== null) window.clearTimeout(armTimer.current);
        setArmed(option.alias);
        armTimer.current = window.setTimeout(() => setArmed(null), ARM_TIMEOUT_MS);
        return;
      }
      void run(option.alias, () => selectScenario(option.alias));
    },
    [armed, run]
  );

  const locked = !control?.enabled || !control?.available;

  return (
    <div className="control-page">
      <header className="topbar">
        <Link className="brand" href="/">KineticPulse</Link>
        <nav className="desktop-nav" aria-label="Primary navigation">
          <Link href="/">Dashboard</Link>
          <Link href="/sessions">Live sessions</Link>
        </nav>
      </header>

      <main className="control-shell">
        <section className="dashboard-intro">
          <div>
            <p className="eyebrow">Bench tooling</p>
            <h1>Scenario control<span>Drive the pipeline by hand.</span></h1>
            <p className="intro-copy">
              Replays a synthetic PRD scenario through the live fusion engine
              without restarting the orchestrator. Telemetry and scripted
              posture share one clock, so both restart together.
            </p>
          </div>
        </section>

        <section className="glass-card control-notice" role="note">
          <span className="signal-icon warning"><AlertTriangle /></span>
          <div>
            <strong>These buttons drive the real emergency path.</strong>
            <p>
              A Tier 2 scenario bypasses voice verification and dispatches
              immediately: webhooks fire to whatever <code>alerts.webhooks</code>
              points at, a WebRTC session opens, and the voice prompt plays.
              Point the config at a test endpoint before using this, and expect
              every activation to appear in the caregiver event feed as a drill.
            </p>
          </div>
        </section>

        {error ? (
          <div className="control-alert" role="alert">{error}</div>
        ) : null}

        {control && locked ? (
          <div className="control-alert warning" role="status">
            {!control.enabled
              ? "Scenario control is switched off. Set monitoring.control_enabled: true in config.yaml and restart the orchestrator."
              : control.reason}
          </div>
        ) : null}

        <section className="glass-card control-state" aria-label="Active scenario">
          <div className="control-state-grid">
            <div>
              <span>Active scenario</span>
              <strong>{control?.scenarioLabel ?? "—"}</strong>
            </div>
            <div>
              <span>Elapsed</span>
              <strong>{control ? `${control.elapsedS.toFixed(1)}s` : "—"}</strong>
            </div>
            <div>
              <span>Telemetry</span>
              <strong>{control?.sensorSource === "mock" ? "Synthetic" : "Hardware"}</strong>
            </div>
            <div>
              <span>Fusion tier</span>
              <strong>{monitoring ? tierLabel(monitoring.emergency.level) : "—"}</strong>
            </div>
            <div>
              <span>Heart rate</span>
              <strong>{monitoring?.heartRate.bpm ?? "—"}</strong>
            </div>
            <div>
              <span>Posture</span>
              <strong>{monitoring?.vision.state.replace(/_/g, " ") ?? "—"}</strong>
            </div>
          </div>
          {control?.drill ? (
            <p className="control-drill-flag" role="status">
              Drill in progress — the dashboard is showing scripted telemetry.
            </p>
          ) : null}
          <div className="control-actions">
            <button
              type="button"
              disabled={locked || busy !== null}
              onClick={() => void run("restart", restartScenario)}
            >
              <RotateCcw aria-hidden="true" /> Replay from t=0
            </button>
            <button
              type="button"
              className="secondary"
              disabled={locked || busy !== null}
              onClick={() => void run("reset", resetScenario)}
            >
              <Square aria-hidden="true" /> Stop (back to resting)
            </button>
          </div>
        </section>

        {GROUP_ORDER.map((group) => {
          const options = (control?.scenarios ?? []).filter((o) => o.group === group);
          if (options.length === 0) return null;
          return (
            <section className="glass-card control-group" key={group} aria-label={GROUP_LABELS[group]}>
              <h2>{GROUP_LABELS[group]}</h2>
              <div className="control-button-grid">
                {options.map((option) => {
                  const active = control?.scenario === option.id;
                  const isArmed = armed === option.alias;
                  return (
                    <button
                      key={option.alias}
                      type="button"
                      className={`scenario-button${active ? " active" : ""}${
                        isHighImpact(option) ? " high-impact" : ""
                      }${isArmed ? " armed" : ""}`}
                      disabled={locked || busy !== null}
                      aria-pressed={active}
                      onClick={() => activate(option)}
                      onBlur={isArmed ? disarm : undefined}
                    >
                      <span className="scenario-button-head">
                        <Play aria-hidden="true" />
                        <strong>{option.label}</strong>
                        {active ? <em className="scenario-live">Running</em> : null}
                      </span>
                      <span className="scenario-tier">{tierLabel(option.expectedTier)}</span>
                      <span className="scenario-description">{option.description}</span>
                      {isArmed ? (
                        <span className="scenario-arm">Press again to confirm — this dispatches</span>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            </section>
          );
        })}
      </main>
    </div>
  );
}
