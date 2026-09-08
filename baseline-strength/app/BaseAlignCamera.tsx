import React, { useState, useRef, useEffect } from 'react';
import { StyleSheet, Text, View, TouchableOpacity, Alert, SafeAreaView } from 'react-native';
import { CameraView, useCameraPermissions, CameraType } from 'expo-camera';
import { shareAsync } from 'expo-sharing';
import * as MediaLibrary from 'expo-media-library';

export default function BaseAlignCamera() {
  const [permission, requestPermission] = useCameraPermissions();
  const [mediaLibraryPermission, requestMediaLibraryPermission] = MediaLibrary.usePermissions();
  const [cameraType, setCameraType] = useState<CameraType>('back');
  const [isRecording, setIsRecording] = useState(false);
  const [videoUri, setVideoUri] = useState<string | null>(null);
  
  const cameraRef = useRef<CameraView>(null);

  useEffect(() => {
    // Auto-request permissions on mount if not determined
    if (!permission?.granted) requestPermission();
    if (!mediaLibraryPermission?.granted) requestMediaLibraryPermission();
  }, []);

  if (!permission) {
    // Camera permissions are still loading
    return <View />;
  }

  if (!permission.granted) {
    // Camera permissions are not granted yet
    return (
      <View style={styles.container}>
        <Text style={{ textAlign: 'center', color: 'white', marginBottom: 20 }}>
          BaseAlign needs access to your camera to capture training footage.
        </Text>
        <TouchableOpacity style={styles.button} onPress={requestPermission}>
          <Text style={styles.text}>Grant Permission</Text>
        </TouchableOpacity>
      </View>
    );
  }

  // START RECORDING
  const startRecording = async () => {
    if (cameraRef.current) {
      try {
        setIsRecording(true);
        console.log('Starting recording...');
        const video = await cameraRef.current.recordAsync({
          maxDuration: 60, // Limit to 60s for "Minimum Effective Dose" clips
          quality: '1080p',
        });
        
        console.log('Recording finished:', video?.uri);
        setVideoUri(video?.uri);
        setIsRecording(false);
        handleSaveVideo(video?.uri);
      } catch (error) {
        console.error('Failed to record:', error);
        setIsRecording(false);
        Alert.alert('Error', 'Failed to start recording.');
      }
    }
  };

  // STOP RECORDING
  const stopRecording = () => {
    if (cameraRef.current && isRecording) {
      console.log('Stopping recording...');
      cameraRef.current.stopRecording();
      setIsRecording(false);
    }
  };

  // SAVE TO GALLERY (Temporary Step before Cloud Upload)
  const handleSaveVideo = async (uri: string) => {
    if (mediaLibraryPermission?.granted) {
      await MediaLibrary.saveToLibraryAsync(uri);
      Alert.alert('Saved', 'Footage saved to gallery. Ready for AI Analysis.');
    } else {
      Alert.alert('Permission needed', 'Cannot save to gallery without permission.');
    }
  };

  return (
    <SafeAreaView style={styles.container}>
      {/* CAMERA VIEW */}
      <CameraView
        style={styles.camera}
        facing={cameraType}
        mode="video"
        ref={cameraRef}
      >
        <View style={styles.overlay}>
          {/* TOP CONTROLS */}
          <View style={styles.topControls}>
            <TouchableOpacity 
              style={styles.flipButton}
              onPress={() => setCameraType(current => (current === 'back' ? 'front' : 'back'))}>
              <Text style={styles.text}>Flip Camera</Text>
            </TouchableOpacity>
          </View>

          {/* RECORD BUTTON */}
          <View style={styles.bottomControls}>
            <TouchableOpacity
              style={[styles.recordButton, isRecording ? styles.recording : {}]}
              onPress={isRecording ? stopRecording : startRecording}
            >
              <View style={styles.recordInner} />
            </TouchableOpacity>
            <Text style={styles.statusText}>
              {isRecording ? 'RECORDING - ACTION MODE' : 'STANDBY'}
            </Text>
          </View>
        </View>
      </CameraView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: 'black',
    justifyContent: 'center',
  },
  camera: {
    flex: 1,
  },
  overlay: {
    flex: 1,
    backgroundColor: 'transparent',
    justifyContent: 'space-between',
    padding: 20,
  },
  topControls: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    marginTop: 40,
  },
  bottomControls: {
    alignItems: 'center',
    marginBottom: 40,
  },
  button: {
    backgroundColor: '#333',
    padding: 15,
    borderRadius: 8,
  },
  flipButton: {
    backgroundColor: 'rgba(0,0,0,0.5)',
    padding: 10,
    borderRadius: 8,
  },
  text: {
    fontSize: 14,
    fontWeight: 'bold',
    color: 'white',
  },
  statusText: {
    color: 'white',
    marginTop: 10,
    fontWeight: '800',
    letterSpacing: 1.5,
  },
  recordButton: {
    width: 80,
    height: 80,
    borderRadius: 40,
    borderWidth: 6,
    borderColor: 'white',
    justifyContent: 'center',
    alignItems: 'center',
  },
  recording: {
    borderColor: 'red',
  },
  recordInner: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: 'red',
  }
});
