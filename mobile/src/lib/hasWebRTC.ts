import { NativeModules } from "react-native";

/** Expo Go has no `react-native-webrtc`. Never statically import that package. */
export const hasWebRTC = NativeModules.WebRTCModule != null;
