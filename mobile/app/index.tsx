import { Link, Stack, useFocusEffect } from "expo-router";
import { useCallback, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";

import { fetchLiveVitals, LiveVitals } from "@/api/monitoring";
import { fetchSessions, formatTime } from "@/api/sessions";
import { FilterChip } from "@/components/FilterChip";
import { InventoryCard } from "@/components/InventoryCard";
import { loadSettings } from "@/storage/settings";
import { colors, radius, spacing, typography } from "@/theme";
import { AppSettings, SessionSummary } from "@/types/session";

type Tone = "normal" | "warning" | "critical";

function words(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function emergencyLabel(level: string): string {
  const labels: Record<string, string> = {
    none: "Clear",
    tier_0_dismiss: "Dismissed",
    tier_1_verify: "Needs verification",
    tier_2_seizure: "Seizure — respond",
    tier_2_cardiac: "Cardiac — respond"
  };
  return labels[level] ?? words(level);
}

function toneOf(vitals: LiveVitals): Tone {
  const tier = vitals.emergencyTier;
  if (tier.startsWith("tier_2") || vitals.fallDetected || vitals.hrStatus === "pulse_lost") {
    return "critical";
  }
  if (tier === "tier_1_verify" || vitals.hrStatus === "elevated" || vitals.hrStatus === "low") {
    return "warning";
  }
  return "normal";
}

function headline(vitals: LiveVitals, tone: Tone): string {
  if (tone === "critical") return "Emergency response active";
  if (vitals.sensorConnection === "disconnected") return "Monitoring has limited coverage";
  if (tone === "warning") return "Verification is in progress";
  return "All monitoring signals are steady";
}

function percent(value: number | null): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function toneColor(tone: Tone): string {
  if (tone === "critical") return colors.error;
  if (tone === "warning") return colors.warning;
  return colors.success;
}

function SessionCard({ session }: { session: SessionSummary }) {
  const meta = session.meta ?? {};
  const tier = meta.tier ?? "";
  const isCritical = tier.includes("tier_2") || tier.includes("2");
  const title = meta.reason?.trim() || (tier ? emergencyLabel(tier) : "Active alert");
  const hrLine =
    meta.heart_rate_bpm != null ? `Heart rate · ${meta.heart_rate_bpm} BPM` : null;

  return (
    <Link
      href={{ pathname: "/session/[id]", params: { id: session.session_id } }}
      asChild
    >
      <InventoryCard
        title={title}
        lines={[
          meta.subject_id && meta.location
            ? `${meta.subject_id} · ${meta.location}`
            : meta.location ?? meta.subject_id,
          hrLine,
          `Started ${formatTime(session.created_at_ms)}`
        ].filter((line): line is string => Boolean(line))}
        ctaLabel="Open alert"
        headerRight={
          tier ? <FilterChip active={isCritical} label={emergencyLabel(tier)} pointerEvents="none" /> : null
        }
      />
    </Link>
  );
}

export default function HomeScreen() {
  const [vitals, setVitals] = useState<LiveVitals | null>(null);
  const [offline, setOffline] = useState(false);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const settingsRef = useRef<AppSettings | null>(null);
  const busyRef = useRef(false);

  const refresh = useCallback(async (showSpinner = false) => {
    if (busyRef.current) return;
    busyRef.current = true;
    if (showSpinner) setRefreshing(true);
    try {
      const cfg = settingsRef.current ?? (await loadSettings());
      settingsRef.current = cfg;
      setLoading(false);

      const live = await fetchLiveVitals(cfg);
      setVitals(live);
      setOffline(false);
      void fetchSessions(cfg).then(setSessions).catch(() => setSessions([]));
    } catch {
      setOffline(true);
    } finally {
      busyRef.current = false;
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      settingsRef.current = null;
      refresh();
      const timer = setInterval(() => refresh(), 3000);
      return () => clearInterval(timer);
    }, [refresh])
  );

  const tone = vitals ? toneOf(vitals) : "warning";
  const accent = toneColor(tone);
  const onDark = tone === "normal";
  const confPct = vitals?.fallConfidence == null ? 0 : Math.max(0, Math.min(100, Math.round(vitals.fallConfidence * 100)));
  const pulseLost = vitals?.hrStatus === "pulse_lost";

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
      <Stack.Screen
        options={{
          title: "KineticPulse",
          headerRight: () => (
            <Link href="/settings" asChild>
              <Pressable hitSlop={12} accessibilityRole="button" accessibilityLabel="Setup">
                <Text style={styles.headerLink}>Setup</Text>
              </Pressable>
            </Link>
          )
        }}
      />

      <View
        style={[
          styles.banner,
          tone === "critical" ? styles.bannerCritical : tone === "warning" ? styles.bannerWarning : styles.bannerNormal
        ]}
      >
        <View style={[styles.bannerMark, { backgroundColor: accent }]}>
          <Text style={styles.bannerMarkText}>{tone === "normal" ? "OK" : "!"}</Text>
        </View>
        <Text style={[styles.kicker, onDark && styles.onDarkMuted]}>Current assessment</Text>
        <Text style={[styles.bannerTitle, onDark && styles.onDark]}>
          {vitals ? headline(vitals, tone) : "Connecting…"}
        </Text>
        <Text style={[styles.bannerCopy, onDark && styles.onDarkMuted]}>
          {vitals ? `${vitals.subjectId} · ${vitals.location}` : "Waiting for monitoring."}
        </Text>
        {vitals?.reason ? (
          <Text style={[styles.bannerReason, onDark && styles.onDark]}>{vitals.reason}</Text>
        ) : null}
        <View style={styles.badgeRow}>
          <View style={[styles.badge, { borderColor: accent }]}>
            <Text style={styles.badgeLabel}>Emergency</Text>
            <Text style={[styles.badgeValue, { color: accent }]}>
              {emergencyLabel(vitals?.emergencyTier ?? "none")}
            </Text>
          </View>
          <View style={[styles.badge, { borderColor: vitals?.fallDetected ? colors.error : colors.success }]}>
            <Text style={styles.badgeLabel}>Fall</Text>
            <Text
              style={[
                styles.badgeValue,
                { color: vitals?.fallDetected ? colors.error : colors.success }
              ]}
            >
              {vitals?.fallDetected ? "Detected" : "Clear"}
            </Text>
          </View>
          {vitals?.drill ? (
            <View style={[styles.badge, styles.badgeDrill]}>
              <Text style={styles.badgeLabel}>Mode</Text>
              <Text style={[styles.badgeValue, { color: colors.surfaceDark }]}>Drill</Text>
            </View>
          ) : null}
        </View>
      </View>

      {vitals?.drill ? (
        <Text style={styles.drillNote}>
          Practice drill. These readings are not from a real event.
        </Text>
      ) : null}

      {offline && !vitals ? (
        <Text style={styles.error}>Can&apos;t reach monitoring. Pull down to retry.</Text>
      ) : null}

      <View style={styles.metrics}>
        <View style={[styles.metricCard, pulseLost && styles.metricCardHot]}>
          <Text style={styles.metricKicker}>Heart rate</Text>
          <Text style={[styles.metricStatus, pulseLost && styles.metricStatusHot]}>
            {vitals ? words(vitals.hrStatus) : "—"}
          </Text>
          {vitals?.bpm == null ? (
            <Text style={[styles.metricGiant, pulseLost && styles.metricGiantHot]}>
              {pulseLost ? "Pulse lost" : "No signal"}
            </Text>
          ) : (
            <View style={styles.metricRow}>
              <Text style={styles.metricGiant}>{vitals.bpm}</Text>
              <Text style={styles.metricUnit}>BPM</Text>
            </View>
          )}
        </View>

        <View style={[styles.metricCard, vitals?.fallDetected && styles.metricCardHot]}>
          <Text style={styles.metricKicker}>Fall confidence</Text>
          <Text style={[styles.metricStatus, vitals?.fallDetected && styles.metricStatusHot]}>
            {vitals?.fallDetected ? "Fall detected" : "No active fall"}
          </Text>
          <View style={styles.metricRow}>
            <Text style={[styles.metricGiant, vitals?.fallDetected && styles.metricGiantHot]}>
              {percent(vitals?.fallConfidence ?? null)}
            </Text>
          </View>
          <View style={styles.barTrack}>
            <View
              style={[
                styles.barFill,
                { width: `${confPct}%`, backgroundColor: accent }
              ]}
            />
          </View>
        </View>
      </View>

      <View style={styles.chipRow}>
        <View style={styles.infoChip}>
          <Text style={styles.infoChipLabel}>Posture</Text>
          <Text style={styles.infoChipValue}>{vitals ? words(vitals.pose) : "—"}</Text>
        </View>
        <View style={styles.infoChip}>
          <Text style={styles.infoChipLabel}>Activity</Text>
          <Text style={styles.infoChipValue}>{vitals?.scenario ? words(vitals.scenario) : "—"}</Text>
        </View>
      </View>

      {vitals && vitals.events.length > 0 ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Recent events</Text>
          <View style={styles.eventPanel}>
            {vitals.events.slice(0, 8).map((e) => {
              const sev =
                e.severity === "critical" ? colors.error : e.severity === "warning" ? colors.warning : colors.muted;
              return (
                <View key={e.id} style={styles.event}>
                  <View style={[styles.eventDot, { backgroundColor: sev }]} />
                  <View style={styles.eventBody}>
                    <Text style={styles.eventTitle}>{e.title}</Text>
                    <Text style={styles.eventMeta}>
                      {formatTime(e.timestamp_ms)}
                      {e.detail ? ` · ${e.detail}` : ""}
                    </Text>
                  </View>
                </View>
              );
            })}
          </View>
        </View>
      ) : null}

      {sessions.length > 0 ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Live sessions</Text>
          {sessions.map((s) => (
            <SessionCard key={s.session_id} session={s} />
          ))}
        </View>
      ) : null}
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
  headerLink: {
    ...typography.labelUppercase,
    color: colors.primary,
    paddingRight: spacing.xs
  },
  banner: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.xl,
    borderBottomWidth: 1,
    borderBottomColor: colors.hairline
  },
  bannerNormal: {
    backgroundColor: colors.surfaceDark
  },
  bannerWarning: {
    backgroundColor: "#fffbeb"
  },
  bannerCritical: {
    backgroundColor: "#fef2f2"
  },
  bannerMark: {
    width: 48,
    height: 48,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing.md
  },
  bannerMarkText: {
    ...typography.titleMd,
    color: colors.onDark
  },
  kicker: {
    ...typography.labelUppercase,
    color: colors.muted,
    marginBottom: spacing.xs
  },
  bannerTitle: {
    ...typography.displayMd,
    color: colors.ink,
    marginBottom: spacing.xs
  },
  bannerCopy: {
    ...typography.bodyMd,
    color: colors.body
  },
  bannerReason: {
    ...typography.titleSm,
    color: colors.ink,
    marginTop: spacing.sm
  },
  onDark: {
    color: colors.onDark
  },
  onDarkMuted: {
    color: colors.onDarkSoft
  },
  badgeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.sm,
    marginTop: spacing.lg
  },
  badge: {
    minWidth: 120,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderWidth: 2,
    borderRadius: radius.sm,
    backgroundColor: colors.canvas
  },
  badgeDrill: {
    borderColor: colors.warning,
    backgroundColor: colors.warning
  },
  badgeLabel: {
    ...typography.labelUppercase,
    color: colors.muted,
    marginBottom: 2
  },
  badgeValue: {
    ...typography.titleSm
  },
  drillNote: {
    ...typography.bodySm,
    color: colors.body,
    backgroundColor: colors.surfaceSoft,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md
  },
  error: {
    ...typography.bodySm,
    color: colors.error,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm
  },
  metrics: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.lg,
    gap: spacing.md
  },
  metricCard: {
    backgroundColor: colors.surfaceCard,
    borderWidth: 1,
    borderColor: colors.hairline,
    padding: spacing.lg
  },
  metricCardHot: {
    borderColor: colors.error,
    backgroundColor: "#fef2f2"
  },
  metricKicker: {
    ...typography.labelUppercase,
    color: colors.muted
  },
  metricStatus: {
    ...typography.titleSm,
    color: colors.ink,
    marginTop: spacing.xxs
  },
  metricStatusHot: {
    color: colors.error
  },
  metricRow: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: spacing.sm,
    marginTop: spacing.sm
  },
  metricGiant: {
    fontFamily: typography.displayMd.fontFamily,
    fontWeight: "700",
    fontSize: 64,
    lineHeight: 68,
    letterSpacing: -2,
    color: colors.ink
  },
  metricGiantHot: {
    color: colors.error,
    fontSize: 40,
    lineHeight: 44
  },
  metricUnit: {
    ...typography.labelUppercase,
    color: colors.muted,
    paddingBottom: 8
  },
  barTrack: {
    height: 14,
    borderRadius: radius.pill,
    backgroundColor: colors.surfaceStrong,
    marginTop: spacing.md,
    overflow: "hidden"
  },
  barFill: {
    height: "100%",
    borderRadius: radius.pill
  },
  chipRow: {
    flexDirection: "row",
    gap: spacing.md,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md
  },
  infoChip: {
    flex: 1,
    backgroundColor: colors.surfaceCard,
    borderWidth: 1,
    borderColor: colors.hairline,
    padding: spacing.md
  },
  infoChipLabel: {
    ...typography.labelUppercase,
    color: colors.muted,
    marginBottom: spacing.xxs
  },
  infoChipValue: {
    ...typography.titleMd,
    color: colors.ink
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
  eventPanel: {
    backgroundColor: colors.surfaceCard,
    borderWidth: 1,
    borderColor: colors.hairline,
    paddingVertical: spacing.xs
  },
  event: {
    flexDirection: "row",
    gap: spacing.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.md
  },
  eventDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    marginTop: 6
  },
  eventBody: {
    flex: 1
  },
  eventTitle: {
    ...typography.titleSm,
    color: colors.ink
  },
  eventMeta: {
    ...typography.caption,
    color: colors.muted,
    marginTop: spacing.xxs
  }
});
