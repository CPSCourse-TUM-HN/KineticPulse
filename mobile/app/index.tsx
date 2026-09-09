import { Link, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";

import {
  activateScenario,
  ControlState,
  fetchControl,
  resetScenario
} from "@/api/control";
import { fetchLiveVitals, LiveVitals, previewStreamUrl } from "@/api/monitoring";
import { fetchSessions, formatTime } from "@/api/sessions";
import { Button } from "@/components/Button";
import { FilterChip } from "@/components/FilterChip";
import { HeroBand } from "@/components/HeroBand";
import { InventoryCard } from "@/components/InventoryCard";
import { PreviewStream } from "@/components/PreviewStream";
import { SpecRow } from "@/components/SpecRow";
import { loadSettings } from "@/storage/settings";
import { colors, radius, spacing, tierSemanticColor, typography } from "@/theme";
import { AppSettings, SessionSummary } from "@/types/session";

function SessionCard({ session }: { session: SessionSummary }) {
  const meta = session.meta ?? {};
  const tier = meta.tier ?? "n/a";
  const scenario = meta.scenario ?? "n/a";
  const isCritical = tier.includes("tier_2") || tier.includes("2");
  const hrLine =
    meta.heart_rate_bpm != null
      ? `HR · ${meta.heart_rate_bpm} BPM${meta.hr_signature ? ` (${meta.hr_signature})` : ""}`
      : null;

  return (
    <Link
      href={{ pathname: "/session/[id]", params: { id: session.session_id } }}
      asChild
    >
      <InventoryCard
        title={session.session_id}
        lines={[
          `Status · ${session.status}`,
          `Scenario · ${scenario}`,
          ...(hrLine ? [hrLine] : []),
          `${meta.subject_id ?? "unknown"} · ${meta.location ?? "unknown"}`,
          `Started ${formatTime(session.created_at_ms)}`
        ]}
        ctaLabel="Open live feed"
        headerRight={<FilterChip active={isCritical} label={tier} pointerEvents="none" />}
      />
    </Link>
  );
}

export default function HomeScreen() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [vitals, setVitals] = useState<LiveVitals | null>(null);
  const [vitalsError, setVitalsError] = useState("");
  const [control, setControl] = useState<ControlState | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [signalingError, setSignalingError] = useState("");
  const [pending, setPending] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  /**
   * The three backends are polled independently. The signaling server (:8787)
   * is optional for the demo — the Jetson's monitoring server (:8790) carries
   * vitals, the detection feed and scenario control on its own, so a signaling
   * outage must not blank the screen.
   */
  const refresh = useCallback(async (showSpinner = false) => {
    if (showSpinner) setRefreshing(true);
    try {
      const cfg = await loadSettings();
      setSettings(cfg);

      const [liveVitals, controlState, sessionList] = await Promise.allSettled([
        fetchLiveVitals(cfg),
        fetchControl(cfg),
        fetchSessions(cfg)
      ]);

      if (liveVitals.status === "fulfilled") {
        setVitals(liveVitals.value);
        setVitalsError("");
      } else {
        setVitalsError(
          liveVitals.reason instanceof Error
            ? liveVitals.reason.message
            : String(liveVitals.reason)
        );
      }

      setControl(controlState.status === "fulfilled" ? controlState.value : null);

      if (sessionList.status === "fulfilled") {
        setSessions(sessionList.value);
        setSignalingError("");
      } else {
        setSessions([]);
        setSignalingError(
          sessionList.reason instanceof Error
            ? sessionList.reason.message
            : String(sessionList.reason)
        );
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      refresh();
      const timer = setInterval(() => refresh(), 3000);
      return () => clearInterval(timer);
    }, [refresh])
  );

  const applyScenario = useCallback(
    async (id: string) => {
      if (!settings) return;
      setPending(id);
      try {
        if (id === "resting") {
          await resetScenario(settings);
        } else {
          await activateScenario(settings, id);
        }
        await refresh();
      } catch (e) {
        setVitalsError(e instanceof Error ? e.message : String(e));
      } finally {
        setPending("");
      }
    },
    [settings, refresh]
  );

  const tier = vitals?.emergencyTier ?? "none";
  const critical = tier !== "none" && !tier.includes("tier_0");
  const vitalsLine = vitals
    ? [
        vitals.bpm != null ? `${vitals.bpm} BPM` : "HR n/a",
        vitals.hrSignature,
        `ESP32 ${vitals.sensorConnection}`,
        vitals.ppgSource === "unknown" ? null : `PPG ${vitals.ppgSource}`
      ]
        .filter(Boolean)
        .join(" · ")
    : null;

  if (loading) {
    return (
      <View style={[styles.container, styles.center]}>
        <ActivityIndicator color={colors.primary} size="large" />
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.scrollContent}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => refresh(true)}
          tintColor={colors.primary}
        />
      }
    >
      <HeroBand
        title="KineticPulse"
        subtitle={
          vitals
            ? `${vitals.subjectId} · ${vitals.location}`
            : "Waiting for the Jetson edge node."
        }
      >
        {vitalsLine ? <Text style={styles.vitalsLine}>{vitalsLine}</Text> : null}
        <View style={styles.badgeRow}>
          <View style={[styles.tierBadge, { borderColor: tierSemanticColor(tier) }]}>
            <Text style={[styles.tierBadgeText, { color: tierSemanticColor(tier) }]}>
              {tier.toUpperCase()}
            </Text>
          </View>
          {control?.drill ? (
            <View style={[styles.tierBadge, styles.drillBadge]}>
              <Text style={[styles.tierBadgeText, styles.drillBadgeText]}>DRILL</Text>
            </View>
          ) : null}
        </View>
        {critical && vitals?.reason ? (
          <Text style={styles.reasonLine}>{vitals.reason}</Text>
        ) : null}
      </HeroBand>

      <View style={styles.toolbar}>
        <Link href="/settings" asChild>
          <Button label="Server settings" variant="secondary" style={styles.toolbarButton} />
        </Link>
        <Link href="/scan" asChild>
          <Button label="Scan setup QR" variant="secondary" style={styles.toolbarButton} />
        </Link>
        {settings ? (
          <Text style={styles.serverHint} numberOfLines={1}>
            {settings.monitoringHttpBase || settings.signalingHttpBase}
          </Text>
        ) : null}
      </View>

      {vitalsError ? <Text style={styles.error}>Monitoring · {vitalsError}</Text> : null}

      {settings ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Live detection feed</Text>
          <PreviewStream
            url={previewStreamUrl(settings)}
            reloadKey={control?.generation ?? 0}
          />
          <Text style={styles.caption}>
            Annotated MJPEG overlay straight from the Jetson — no WebRTC session required.
          </Text>
        </View>
      ) : null}

      <View style={styles.section}>
        <Text style={styles.sectionLabel}>Fusion snapshot</Text>
        <View style={styles.panel}>
          <SpecRow
            label="Heart rate"
            value={vitals?.bpm == null ? undefined : `${vitals.bpm} BPM (${vitals.hrStatus})`}
          />
          <SpecRow label="HR signature" value={vitals?.hrSignature} />
          <SpecRow
            label="PPG source"
            value={
              vitals ? `${vitals.ppgSource}${vitals.hrSimulated ? " · simulated" : ""}` : undefined
            }
          />
          <SpecRow label="Pose" value={vitals?.pose} />
          <SpecRow
            label="Accel"
            value={
              vitals?.accelG == null
                ? vitals?.accel
                : `${vitals.accelG.toFixed(2)} g (${vitals.accel})`
            }
          />
          <SpecRow
            label="Detector"
            value={
              vitals?.detectorClass
                ? `${vitals.detectorClass}${
                    vitals.detectorConf != null ? ` · ${vitals.detectorConf.toFixed(2)}` : ""
                  }`
                : undefined
            }
          />
          <SpecRow
            label="Action"
            value={
              vitals?.actionClass
                ? `${vitals.actionClass}${
                    vitals.actionConf != null ? ` · ${vitals.actionConf.toFixed(2)}` : ""
                  }`
                : undefined
            }
          />
          <SpecRow label="Scenario" value={vitals?.scenario} />
          <SpecRow label="Updated" value={formatTime(vitals?.updatedAtMs ?? 0)} last />
        </View>
      </View>

      {control?.enabled ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Scenario control</Text>
          <Text style={styles.caption}>
            Replays scripted telemetry on the Jetson from t=0. Active · {control.scenarioLabel} (
            {control.sensorSource})
            {control.available ? "" : ` — unavailable: ${control.reason}`}
          </Text>
          <View style={styles.chipWrap}>
            {control.scenarios.map((s) => (
              <FilterChip
                key={s.id}
                label={pending === s.id ? "…" : s.label}
                active={control.scenario === s.id}
                disabled={Boolean(pending) || !control.available}
                onPress={() => applyScenario(s.id)}
                style={styles.chip}
              />
            ))}
          </View>
        </View>
      ) : null}

      {vitals && vitals.events.length > 0 ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Recent events</Text>
          <View style={styles.panel}>
            {vitals.events.slice(0, 6).map((e, i, arr) => (
              <View
                key={e.id}
                style={[styles.event, i < arr.length - 1 && styles.eventBorder]}
              >
                <Text style={styles.eventTitle}>{e.title}</Text>
                <Text style={styles.eventMeta}>
                  {e.severity} · {e.category} · {formatTime(e.timestamp_ms)}
                </Text>
              </View>
            ))}
          </View>
        </View>
      ) : null}

      {sessions.length > 0 || signalingError ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Active sessions</Text>
          {sessions.map((s) => (
            <SessionCard key={s.session_id} session={s} />
          ))}
          {sessions.length === 0 ? (
            <Text style={styles.caption}>
              Signaling server unreachable ({signalingError}). WebRTC triage is offline; the
              detection feed above is unaffected.
            </Text>
          ) : null}
        </View>
      ) : null}

      <View style={styles.footer}>
        <Text style={styles.footerText}>KineticPulse · Edge-AI fall detection</Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.canvas
  },
  center: {
    alignItems: "center",
    justifyContent: "center"
  },
  scrollContent: {
    paddingBottom: spacing.xl
  },
  toolbar: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
    gap: spacing.sm,
    backgroundColor: colors.surfaceSoft,
    borderBottomWidth: 1,
    borderBottomColor: colors.hairline
  },
  toolbarButton: {
    alignSelf: "flex-start",
    paddingHorizontal: spacing.lg
  },
  serverHint: {
    ...typography.caption,
    color: colors.muted
  },
  vitalsLine: {
    ...typography.caption,
    color: colors.onDarkSoft,
    marginTop: spacing.md
  },
  badgeRow: {
    flexDirection: "row",
    gap: spacing.xs,
    marginTop: spacing.sm
  },
  tierBadge: {
    borderWidth: 1,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xxs
  },
  tierBadgeText: {
    ...typography.caption
  },
  drillBadge: {
    borderColor: colors.warning,
    backgroundColor: colors.warning
  },
  drillBadgeText: {
    color: colors.surfaceDark
  },
  reasonLine: {
    ...typography.caption,
    color: colors.onDark,
    marginTop: spacing.sm
  },
  error: {
    ...typography.bodySm,
    color: colors.error,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm
  },
  section: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.lg,
    gap: spacing.sm
  },
  sectionLabel: {
    ...typography.labelUppercase,
    color: colors.muted
  },
  caption: {
    ...typography.caption,
    color: colors.muted
  },
  panel: {
    backgroundColor: colors.surfaceCard,
    borderWidth: 1,
    borderColor: colors.hairline,
    paddingHorizontal: spacing.md
  },
  chipWrap: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.xs
  },
  chip: {
    marginBottom: spacing.xxs
  },
  event: {
    paddingVertical: spacing.md
  },
  eventBorder: {
    borderBottomWidth: 1,
    borderBottomColor: colors.hairline
  },
  eventTitle: {
    ...typography.bodySm,
    color: colors.ink
  },
  eventMeta: {
    ...typography.caption,
    color: colors.muted,
    marginTop: spacing.xxs
  },
  footer: {
    marginTop: spacing.lg,
    backgroundColor: colors.surfaceSoft,
    paddingVertical: spacing.lg,
    paddingHorizontal: spacing.lg,
    borderTopWidth: 1,
    borderTopColor: colors.hairline
  },
  footerText: {
    ...typography.bodySm,
    color: colors.muted
  }
});
