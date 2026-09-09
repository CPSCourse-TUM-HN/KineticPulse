import {
  Inter_300Light,
  Inter_400Regular,
  Inter_700Bold,
  useFonts
} from "@expo-google-fonts/inter";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { useEffect, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { colors, typography } from "@/theme";

SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const [loaded, fontError] = useFonts({
    Inter_300Light,
    Inter_400Regular,
    Inter_700Bold
  });
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (loaded || fontError) setReady(true);
  }, [loaded, fontError]);

  useEffect(() => {
    const t = setTimeout(() => setReady(true), 2500);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (ready) SplashScreen.hideAsync();
  }, [ready]);

  if (!ready) {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.canvas }}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: colors.canvas },
          headerTintColor: colors.ink,
          headerTitleStyle: { ...typography.titleSm, color: colors.ink },
          headerShadowVisible: false,
          headerBackTitle: "Back",
          contentStyle: { backgroundColor: colors.canvas }
        }}
      >
        <Stack.Screen name="index" options={{ title: "KineticPulse" }} />
        <Stack.Screen name="settings" options={{ title: "Setup" }} />
        <Stack.Screen name="scan" options={{ title: "Scan code" }} />
        <Stack.Screen
          name="session/[id]"
          options={{
            title: "Alert",
            headerBackTitle: "Back"
          }}
        />
      </Stack>
    </SafeAreaProvider>
  );
}
