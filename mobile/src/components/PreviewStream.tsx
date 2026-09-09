import { StyleSheet, Text, View, type StyleProp, type ViewStyle } from "react-native";
import { WebView } from "react-native-webview";

import { colors, radius, spacing, typography } from "@/theme";

type Props = {
  url: string;
  /** Bump to force a reconnect after the feed has stopped (Jetson answers 503). */
  reloadKey?: number;
  style?: StyleProp<ViewStyle>;
};

/**
 * The Jetson's annotated detection feed.
 *
 * `/preview.mjpg` is `multipart/x-mixed-replace`, which React Native's `Image`
 * cannot decode (Fresco on Android has no multipart support) — but any browser
 * engine renders it in a plain `<img>`, so the frames go through a WebView.
 * This deliberately avoids the WebRTC path: no signaling server, no session, no
 * ICE negotiation, which is what makes it usable for a demo.
 */
export function PreviewStream({ url, reloadKey = 0, style }: Props) {
  const html = `<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  html,body{margin:0;height:100%;background:${colors.surfaceDark};overflow:hidden}
  img{width:100%;height:100%;object-fit:cover;display:block}
</style>
<img src="${url}" alt="">`;

  return (
    <View style={[styles.shell, style]}>
      <WebView
        key={`${url}#${reloadKey}`}
        source={{ html }}
        originWhitelist={["*"]}
        style={styles.web}
        containerStyle={styles.web}
        scrollEnabled={false}
        javaScriptEnabled={false}
        domStorageEnabled={false}
        setSupportMultipleWindows={false}
        mixedContentMode="always"
        androidLayerType="hardware"
        allowsInlineMediaPlayback
        renderError={() => (
          <View style={styles.fallback}>
            <Text style={styles.fallbackText}>Detection feed unavailable</Text>
          </View>
        )}
      />
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
  web: {
    flex: 1,
    backgroundColor: colors.surfaceDark
  },
  fallback: {
    flex: 1,
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
