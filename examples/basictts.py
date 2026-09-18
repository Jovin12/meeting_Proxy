import scipy.io.wavfile
import torch
from pocket_tts import TTSModel

device = "cuda" if torch.cuda.is_available() else "cpu"
model = TTSModel.load_model().to(device)
voice_state = model.get_state_for_audio_prompt("alba")

audio_data = model.generate_audio(
    model_state = voice_state,
    text_to_generate = "Hello! This is a sample example of how to run this locally.",
)

scipy.io.wavfile.write("output.wav", model.sample_rate, audio_data.cpu().numpy())
print("Audio Saved Successfully to output.wav!")

