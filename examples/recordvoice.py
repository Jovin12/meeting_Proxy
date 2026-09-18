import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import numpy as np
import scipy.io.wavfile
import sounddevice as sd


OUTPUT_PATH = Path("my_recorded_voice.wav")
SAMPLE_RATE = 24000


class VoiceRecorder:
	def __init__(self, root):
		self.root = root
		self.root.title("Record Voice")
		self.root.resizable(False, False)
		self.recording = False
		self.stream = None
		self.frames = []

		panel = ttk.Frame(root, padding=18)
		panel.grid()

		ttk.Label(panel, text="Record your voice", font=("Segoe UI", 16, "bold")).grid(
			row=0, column=0, pady=(0, 10)
		)
		self.record_button = ttk.Button(
			panel, text="Start recording", command=self.toggle_recording
		)
		self.record_button.grid(row=1, column=0, sticky="ew")

		self.status = ttk.Label(panel, text="Click Start recording, then speak.")
		self.status.grid(row=2, column=0, pady=(10, 0))

	def toggle_recording(self):
		if self.recording:
			self.stop_recording()
		else:
			self.start_recording()

	def start_recording(self):
		try:
			self.frames = []
			self.stream = sd.InputStream(
				samplerate=SAMPLE_RATE,
				channels=1,
				dtype="float32",
				callback=self.capture_audio,
			)
			self.stream.start()
		except Exception as error:
			messagebox.showerror("Recording error", str(error))
			self.stream = None
			return

		self.recording = True
		self.record_button.configure(text="Stop recording")
		self.status.configure(text="Recording... click Stop recording when finished.")

	def capture_audio(self, input_data, frame_count, time_info, status):
		self.frames.append(input_data.copy())

	def stop_recording(self):
		self.recording = False
		self.stream.stop()
		self.stream.close()
		self.stream = None

		if not self.frames:
			self.record_button.configure(text="Start recording")
			self.status.configure(text="No audio was captured.")
			return

		audio = np.concatenate(self.frames, axis=0).squeeze()
		audio = np.clip(audio, -1, 1)
		scipy.io.wavfile.write(
			OUTPUT_PATH,
			SAMPLE_RATE,
			(audio * 32767).astype(np.int16),
		)
		self.record_button.configure(text="Record again")
		self.status.configure(text=f"Saved recording to {OUTPUT_PATH}")
		messagebox.showinfo("Recording saved", f"Saved recording to {OUTPUT_PATH}")


if __name__ == "__main__":
	window = tk.Tk()
	VoiceRecorder(window)
	window.mainloop()
