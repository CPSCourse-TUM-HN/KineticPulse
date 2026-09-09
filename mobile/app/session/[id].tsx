import { useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { LiveFeed } from "@/components/LiveFeed";
import { FilterChip } from "@/components/FilterChip";
import { HeroBand } from "@/components/HeroBand";
import { SpecRow } from "@/components/SpecRow";
import { useCaregiverPeer } from "@/hooks/useCaregiverPeer";
import { loadSettings } from "@/storage/settings";
import { colors, radius, spacing, typography } from "@/theme";
import { AppSettings } from "@/types/session";

export default function SessionScreen() {
  const params = useLocalSearchParams<{ id: string }>();
  const sessionId = decodeURIComponent(params.id ?? "");
  const [settings, setSettings] = useState<AppSettings | null>(null);

  useEffect(() => {
    loadSettings().then(setSettings);
  }, []);

  const { connectionState, remoteStream, sessionMeta, error } = useCaregiverPeer({
    sessionId,
    settings,
    enabled: Boolean(sessionId && settings)
  });

  const connected = connectionState === "connected";
  const failed = connectionState === "failed";
  const statusColor = connected ? colors.success : failed ? colors.error : colors.warning;
  const tier = sessionMeta?.tier;
  const isCritical = Boolean(tier && (tier.includes("tier_2") || tier.includes("2")));

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <HeroBand
        title={sessionMeta?.reason?.trim() || "Alert"}
        subtitle={
          [sessionMeta?.subject_id, sessionMeta?.location].filter(Boolean).join(" · ") || undefined
        }
      >
        <View style={styles.statusRow}>
          <View style={[styles.statusDot, { backgroundColor: statusColor }]} />
          <Text style={styles.statusText}>
            {connected ? "Live camera connected" : failed ? "Camera unavailable" : "Connecting…"}
          </Text>
          {tier ? (
            <FilterChip active={isCritical} label={tier.replace(/_/g, " ")} pointerEvents="none" style={styles.tierChip} />
          ) : null}
        </View>
      </HeroBand>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <View style={styles.videoSection}>
        <Text style={styles.sectionLabel}>Scene</Text>
        <View style={styles.videoShell}>
          <LiveFeed
            streamURL={remoteStream?.toURL()}
            connecting={connectionState === "connecting"}
          />
        </View>
      </View>

      <View style={styles.specPanel}>
        <Text style={styles.sectionLabel}>Alert</Text>
        <SpecRow label="Person" value={sessionMeta?.subject_id} />
        <SpecRow label="Location" value={sessionMeta?.location} />
        <SpecRow label="What happened" value={sessionMeta?.reason} />
        <SpecRow
          label="Heart rate"
          value={
            sessionMeta?.heart_rate_bpm == null
              ? undefined
              : `${sessionMeta.heart_rate_bpm} BPM`
          }
          last
        />
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.canvas
  },
  content: {
    paddingBottom: spacing.xl
  },
  statusRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginTop: spacing.md,
    flexWrap: "wrap"
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4
  },
  statusText: {
    ...typography.caption,
    color: colors.onDarkSoft,
    flex: 1
  },
  tierChip: {
    marginLeft: "auto"
  },
  error: {
    ...typography.bodySm,
    color: colors.error,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm
  },
  videoSection: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.lg
  },
  sectionLabel: {
    ...typography.labelUppercase,
    color: colors.muted,
    marginBottom: spacing.sm
  },
  videoShell: {
    borderRadius: radius.none,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: colors.hairline,
    backgroundColor: colors.surfaceCard,
    aspectRatio: 16 / 9
  },
  specPanel: {
    marginHorizontal: spacing.lg,
    marginTop: spacing.lg,
    paddingHorizontal: spacing.lg,
    backgroundColor: colors.canvas,
    borderWidth: 1,
    borderColor: colors.hairline
  }
});
