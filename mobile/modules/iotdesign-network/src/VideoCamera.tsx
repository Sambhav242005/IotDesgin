import type { ComponentType } from 'react';
import { Platform, View, type ViewProps } from 'react-native';
import { requireNativeViewManager } from 'expo-modules-core';

type Props = ViewProps & {
  configuration: { address: string; token: string; fingerprint: string } | null;
  onVideoStatus?: (event: { nativeEvent: { state: string; detail: string } }) => void;
};

const NativeVideo: ComponentType<Props> | null = Platform.OS === 'android'
  ? requireNativeViewManager('IoTDesignNetwork') : null;

export function VideoCamera(props: Props) {
  return NativeVideo ? <NativeVideo {...props} /> : <View style={props.style} />;
}
