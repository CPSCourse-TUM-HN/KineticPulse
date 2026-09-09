import { useEffect, useRef, useState } from "react";
import { Image, StyleSheet, Text, View, type StyleProp, type ViewStyle } from "react-native";

import { colors, radius, spacing, typography } from "@/theme";

type Props = {
  url: string;
  /** Bump to force a cache-bust after the Jetson switches scenario. */
  reloadKey?: number;
  style?: StyleProp<ViewStyle>;
};

const LIVE_MS = 500;
const RETRY_MS = 3000;
const HANG_MS = 8000;

/**
 * The Jetson's annotated detection feed as JPEG poll.
 *
 * One in-flight Image at a time. A 500 ms interval against an unreachable
 * host piles TCP connections until Android freezes the app.
 */
export function PreviewStream({ url, reloadKey = 0, style }: Props) {
  const [tick, setTick] = useState(0);
  const [live, setLive] = useState(false);
  const nextTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hangTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const done = useRef(false);

  const clearNext = () => {
    if (nextTimer.current) {
      clearTimeout(nextTimer.current);
      nextTimer.current = null;
    }
  };

  const clearHang = () => {
    if (hangTimer.current) {
      clearTimeout(hangTimer.current);
      hangTimer.current = null;
    }
  };

  useEffect(() => {
    setTick(0);
    setLive(false);
    return () => {
      clearNext();
      clearHang();
    };
  }, [url, reloadKey]);

  const uri = `${url}?g=${reloadKey}&t=${tick}`;

  useEffect(() => {
    done.current = false;
    clearHang();
    hangTimer.current = setTimeout(() => {
      if (done.current) return;
      done.current = true;
      setLive(false);
      nextTimer.current = setTimeout(() => setTick((n) => n + 1), RETRY_MS);
    }, HANG_MS);
    return clearHang;
  }, [uri]);

  const onDone = (ok: boolean) => {
    if (done.current) return;
    done.current = true;
    clearHang();
    setLive(ok);
    clearNext();
    nextTimer.current = setTimeout(() => setTick((n) => n + 1), ok ? LIVE_MS : RETRY_MS);
  };

  return (
    <View style={[styles.shell, style]}>
      <Image
        source={{ uri }}
        style={styles.image}
        resizeMode="cover"
        onLoad={() => onDone(true)}
        onError={() => onDone(false)}
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
