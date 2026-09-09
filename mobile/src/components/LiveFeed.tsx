import { type ComponentType, useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { hasWebRTC } from "@/lib/hasWebRTC";
import { colors, spacing, typography } from "@/theme";

type Props = {
  streamURL?: string;
  connecting?: boolean;
};

/** RTCView is loaded only when the native module exists. */
export function LiveFeed({ streamURL, connecting }: Props) {
  const [RTCView, setRTCView] = useState<ComponentType<{
    streamURL: string;
    style: object;
    objectFit: string;
    mirror: boolean;
  }> | null>(null);

  useEffect(() => {
    if (!hasWebRTC) return;
    import("react-native-webrtc")
      .then((mod) => setRTCView(() => mod.RTCView as never))
      .catch(() => {});
  }, []);

  if (streamURL && RTCView) {
    return <RTCView streamURL={streamURL} style={styles.video} objectFit="cover" mirror={false} />;
  }

  return (
    <View style={styles.placeholder}>
      {connecting ? <ActivityIndicator color={colors.primary} size="large" /> : null}
      <Text style={styles.text}>
        {hasWebRTC
          ? connecting
            ? "Connecting to Jetson feed…"
            : "Waiting for remote video track"
          : "Video is off in Expo Go. Vitals and scenario control still work."}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  video: { width: "100%", height: "100%" },
  placeholder: {
    flex: 1,
    minHeight: 220,
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.md,
    backgroundColor: colors.surfaceCard,
    padding: spacing.lg
  },
  text: { ...typography.bodySm, color: colors.muted, textAlign: "center" }
});
