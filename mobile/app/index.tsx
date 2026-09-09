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
import { HeroBand } from "@/components/HeroBand";
import { InventoryCard } from "@/components/InventoryCard";
import { SpecRow } from "@/components/SpecRow";
import { loadSettings } from "@/storage/settings";
import { colors, radius, spacing, tierSemanticColor, typography } from "@/theme";
import { AppSettings, SessionSummary } from "@/types/session";

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

function headline(vitals: LiveVitals): string {
  const tier = vitals.emergencyTier;
  if (tier.startsWith("tier_2") || vitals.fallDetected) return "Emergency response active";
  if (vitals.sensorConnection === "disconnected") return "Monitoring has limited coverage";
  if (tier === "tier_1_verify") return "Verification is in progress";
  return "All monitoring signals are steady";
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

      const [liveVitals, sessionList] = await Promise.allSettled([
        fetchLiveVitals(cfg),
        fetchSessions(cfg)
      ]);

      if (liveVitals.status === "fulfilled") {
        setVitals(liveVitals.value);
        setOffline(false);
      } else {
        setOffline(true);
      }

      if (sessionList.status === "fulfilled") setSessions(sessionList.value);
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

  const tier = vitals?.emergencyTier ?? "none";

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

      <HeroBand
        title={vitals ? headline(vitals) : "Connecting…"}
        subtitle={
          vitals ? `${vitals.subjectId} · ${vitals.location}` : "Waiting for monitoring."
        }
      >
        <View style={styles.badgeRow}>
          <View style={[styles.tierBadge, { borderColor: tierSemanticColor(tier) }]}>
            <Text style={[styles.tierBadgeText, { color: tierSemanticColor(tier) }]}>
              {emergencyLabel(tier)}
            </Text>
          </View>
          {vitals?.drill ? (
            <View style={[styles.tierBadge, styles.drillBadge]}>
              <Text style={[styles.tierBadgeText, styles.drillBadgeText]}>DRILL</Text>
            </View>
          ) : null}
        </View>
        {vitals?.reason ? <Text style={styles.reasonLine}>{vitals.reason}</Text> : null}
      </HeroBand>

      {vitals?.drill ? (
        <Text style={styles.drillNote}>
          Practice drill. These readings are not from a real event.
        </Text>
      ) : null}

      {offline && !vitals ? (
        <Text style={styles.error}>Can&apos;t reach monitoring. Pull down to retry.</Text>
      ) : null}

      <View style={styles.section}>
        <Text style={styles.sectionLabel}>Now</Text>
        <View style={styles.panel}>
          <SpecRow
            label="Heart rate"
            value={
              vitals?.bpm == null
                ? vitals?.hrStatus === "pulse_lost"
                  ? "Pulse lost"
                  : "No signal"
                : `${vitals.bpm} BPM`
            }
          />
          <SpecRow
            label="Fall"
            value={vitals?.fallDetected ? "Detected" : "Clear"}
          />
          <SpecRow label="Posture" value={vitals ? words(vitals.pose) : undefined} />
          <SpecRow
            label="Activity"
            value={vitals?.scenario ? words(vitals.scenario) : undefined}
            last
          />
        </View>
      </View>

      {vitals && vitals.events.length > 0 ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Recent events</Text>
          <View style={styles.panel}>
            {vitals.events.slice(0, 8).map((e, i, arr) => (
              <View key={e.id} style={[styles.event, i < arr.length - 1 && styles.eventBorder]}>
                <Text style={styles.eventTitle}>{e.title}</Text>
                <Text style={styles.eventMeta}>
                  {formatTime(e.timestamp_ms)}
                  {e.detail ? ` · ${e.detail}` : ""}
                </Text>
              </View>
            ))}
          </View>
        </View>
      ) : null}

      {sessions.length > 0 ? (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Active alerts</Text>
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
  badgeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
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
  section: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.lg,
    gap: spacing.sm
  },
  sectionLabel: {
    ...typography.labelUppercase,
    color: colors.muted
  },
  panel: {
    backgroundColor: colors.surfaceCard,
    borderWidth: 1,
    borderColor: colors.hairline,
    paddingHorizontal: spacing.md
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
  }
});
