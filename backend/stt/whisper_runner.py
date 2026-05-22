import sounddevice as sd
import wave
import subprocess
import os

def record_audio(filename="temp_audio.wav", duration=5, fs=16000):
    print("Listening...")
    recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype='int16')
    sd.wait()
    print("Done recording.")
    
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(fs)
        wf.writeframes(recording.tobytes())
    return filename

def transcribe_audio(filename="temp_audio.wav"):
    """
    Calls out to a local whisper.cpp executable.
    Assumes 'main.exe' is available in the current PATH or root dir.
    """
    try:
        # Change this path to wherever whisper.cpp main.exe is located
        whisper_cmd = ["main", "-m", "models/ggml-base.en.bin", "-f", filename, "-nt"]
        result = subprocess.run(whisper_cmd, capture_output=True, text=True)
        return result.stdout.strip()
    except FileNotFoundError:
        # Fallback fake STT if whisper is not compiled yet
        print("whisper.cpp executable not found. Mocking STT.")
        return "open chrome"
