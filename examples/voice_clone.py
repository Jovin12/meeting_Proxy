import scipy.io.wavfile
import torch

from pocket_tts import TTSModel


# Use GPU if available
device = "cuda" if torch.cuda.is_available() else "cpu"

print(f"Using device: {device}")


# Load Pocket TTS
model = TTSModel.load_model().to(device)


# Load your reference voice
print("Loading reference voice...")

voice_state = model.get_state_for_audio_prompt(
    "my_recorded_voice.wav"
)


# Generate speech
print("Generating speech...")

audio = model.generate_audio(
    voice_state,
    "This is my own voice being cloned locally in real-time. No one and I mean no one has the ability to clone. "
)


# Save audio
scipy.io.wavfile.write(
    "clone_output.wav",
    model.sample_rate,
    audio.cpu().numpy()
)


print("Voice cloning complete!")
print("Saved to: clone_output.wav")