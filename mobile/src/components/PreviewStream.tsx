import { useEffect, useState } from "react";
import { Image, StyleSheet, Text, View, type StyleProp, type ViewStyle } from "react-native";

import { colors, radius, spacing, typography } from "@/theme";

type Props = {
  url: string;
  /** Bump to force a cache-bust after the Jetson switches scenario. */
  reloadKey?: number;
  style?: StyleProp<ViewStyle>;
};

const FRAME_MS = 500;

/**
 * The Jetson's annotated detection feed.
 *
 * `/preview.mjpg` is `multipart/x-mixed-replace`. React Native Image cannot
 * decode that, and Android WebView usually cannot either — which is why this
 * polls `/preview.jpg` (the same JPEG the stream is built from). That path
 * also does not count against the MJPEG client cap the dashboard uses.
 */
export function PreviewStream({ url, reloadKey = 0, style }: Props) {
  const [tick, setTick] = useState(0);
  const [live, setLive] = useState(false);

  useEffect(() => {
    setTick(0);
    setLive(false);
    const id = setInterval(() => setTick((n) => n + 1), FRAME_MS);
    return () => clearInterval(id);
  }, [url, reloadKey]);

  const uri = `${url}?g=${reloadKey}&t=${tick}`;

  return (
    <View style={[styles.shell, style]}>
      <Image
        source={{ uri }}
        style={styles.image}
        resizeMode="cover"
        onLoad={() => setLive(true)}
        onError={() => setLive(false)}
      />
      {live ? null : (
        <View style={styles.fallback}>
          <Text style={styles.fallbackText}>Waiting for detection feed</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  shell: {
    aspectRatio: 16 / 9,
    borderRadius: radius.sm,
    overflow: "hidden",
    backgroundColor: colors.surfaceDark
  },
  image: {
    ...StyleSheet.absoluteFillObject
  },
  fallback: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
    padding: spacing.lg,
    backgroundColor: colors.surfaceDark
  },
  fallbackText: {
    ...typography.bodySm,
    color: colors.onDarkSoft
  }
});
