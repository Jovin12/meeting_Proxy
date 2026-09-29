from io import BytesIO
from threading import Lock

import scipy.io.wavfile
import torch
from pocket_tts import TTSModel

_model = None
_voice_state = None
_model_lock = Lock()


def robotic_tts(text: str) -> bytes:
    global _model, _voice_state

    with _model_lock:
        if _model is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            _model = TTSModel.load_model().to(device)
            _voice_state = _model.get_state_for_audio_prompt("alba")

        audio_data = _model.generate_audio(
            model_state=_voice_state,
            text_to_generate=text,
        )

    audio_file = BytesIO()
    scipy.io.wavfile.write(
        audio_file,
        _model.sample_rate,
        audio_data.detach().cpu().numpy(),
    )
    return audio_file.getvalue()

