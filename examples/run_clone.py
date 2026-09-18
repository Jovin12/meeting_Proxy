import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import numpy as np
import scipy.io.wavfile
import sounddevice as sd
import torch
from pocket_tts import TTSModel


RECORDING_PATH = Path("my_recorded_voice.wav")
OUTPUT_PATH = Path("clone_output.wav")
SAMPLE_RATE = 24000

BACKGROUND = "#101722"
PANEL = "#172232"
PANEL_LIGHT = "#203047"
GOLD = "#d8b36a"
GOLD_LIGHT = "#f0d69a"
TEXT = "#f5f1e8"
MUTED = "#aeb8c7"
SUCCESS = "#8ed1b2"
DANGER = "#ed9a9a"


class CloneStudio:
    def __init__(self, root):
        self.root = root
        self.root.title("Voice Atelier")
        self.root.configure(bg=BACKGROUND)
        self.root.geometry("720x680")
        self.root.minsize(620, 620)

        self.recording = False
        self.stream = None
        self.frames = []
        self.output_ready = False

        self.build_ui()

    def build_ui(self):
        header = tk.Frame(self.root, bg=BACKGROUND)
        header.pack(fill="x", padx=42, pady=(34, 20))

        tk.Label(
            header,
            text="VOICE ATELIER",
            bg=BACKGROUND,
            fg=GOLD,
            font=("Georgia", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header,
            text="Your voice, beautifully rendered.",
            bg=BACKGROUND,
            fg=TEXT,
            font=("Georgia", 27),
        ).pack(anchor="w", pady=(7, 0))
        tk.Label(
            header,
            text="Record a voice sample, then write the words you want it to speak.",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(9, 0))

        body = tk.Frame(self.root, bg=PANEL)
        body.pack(fill="both", expand=True, padx=42, pady=(0, 30))

        record_section = tk.Frame(body, bg=PANEL)
        record_section.pack(fill="x", padx=28, pady=(26, 20))
        self.section_label(record_section, "01", "CAPTURE YOUR VOICE")
        tk.Label(
            record_section,
            text="A clear sample of a few seconds works best.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(8, 14))

        self.record_button = tk.Button(
            record_section,
            text="recordvoice",
            command=self.toggle_recording,
            bg=GOLD,
            fg=BACKGROUND,
            activebackground=GOLD_LIGHT,
            activeforeground=BACKGROUND,
            relief="flat",
            bd=0,
            padx=22,
            pady=11,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        )
        self.record_button.pack(anchor="w")

        self.record_status = tk.Label(
            record_section,
            text="No voice sample recorded yet.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
        )
        self.record_status.pack(anchor="w", pady=(11, 0))

        self.divider(body)

        prompt_section = tk.Frame(body, bg=PANEL)
        prompt_section.pack(fill="both", expand=True, padx=28, pady=(20, 18))
        self.section_label(prompt_section, "02", "WRITE YOUR SCRIPT")
        tk.Label(
            prompt_section,
            text="Anything you type here will be spoken by the recorded voice.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(8, 10))

        self.prompt = tk.Text(
            prompt_section,
            height=5,
            wrap="word",
            bg=PANEL_LIGHT,
            fg=TEXT,
            insertbackground=GOLD_LIGHT,
            selectbackground=GOLD,
            selectforeground=BACKGROUND,
            relief="flat",
            padx=14,
            pady=12,
            font=("Segoe UI", 11),
        )
        self.prompt.pack(fill="both", expand=True)
        self.prompt.insert("1.0", "This is my voice speaking a new message, created locally.")

        footer = tk.Frame(self.root, bg=BACKGROUND)
        footer.pack(fill="x", padx=42, pady=(0, 28))
        actions = tk.Frame(footer, bg=BACKGROUND)
        actions.pack(side="right")

        self.submit_button = tk.Button(
            actions,
            text="SUBMIT TEXT  →",
            command=self.start_clone,
            bg=GOLD,
            fg=BACKGROUND,
            activebackground=GOLD_LIGHT,
            activeforeground=BACKGROUND,
            disabledforeground="#756643",
            relief="flat",
            bd=0,
            padx=20,
            pady=12,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        )
        self.submit_button.pack(side="left", padx=(0, 8))

        self.play_button = tk.Button(
            actions,
            text="PLAY WAV  ▶",
            command=self.play_output,
            bg=PANEL_LIGHT,
            fg=TEXT,
            activebackground="#30445e",
            activeforeground=TEXT,
            disabledforeground="#657286",
            relief="flat",
            bd=0,
            padx=20,
            pady=12,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        )
        self.play_button.pack(side="left")

        self.status = tk.Label(
            footer,
            text="Ready when you are.",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 9),
        )
        self.status.pack(side="left", pady=12)

    def section_label(self, parent, number, title):
        line = tk.Frame(parent, bg=PANEL)
        line.pack(fill="x")
        tk.Label(
            line,
            text=number,
            bg=PANEL,
            fg=GOLD,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(0, 12))
        tk.Label(
            line,
            text=title,
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

    def divider(self, parent):
        tk.Frame(parent, bg="#2c3b4f", height=1).pack(fill="x", padx=28)

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
            messagebox.showerror("Microphone unavailable", str(error))
            self.stream = None
            return

        self.recording = True
        self.record_button.configure(text="stop recording", bg="#d98686")
        self.submit_button.configure(state="disabled")
        self.play_button.configure(state="disabled")
        self.record_status.configure(text="Recording now... click again when finished.", fg=DANGER)
        self.status.configure(text="Listening...", fg=DANGER)

    def capture_audio(self, input_data, frame_count, time_info, status):
        self.frames.append(input_data.copy())

    def stop_recording(self):
        self.recording = False
        self.stream.stop()
        self.stream.close()
        self.stream = None

        if not self.frames:
            self.record_button.configure(text="recordvoice", bg=GOLD)
            self.record_status.configure(text="No audio was captured.", fg=DANGER)
            return

        audio = np.concatenate(self.frames, axis=0).squeeze()
        audio = np.clip(audio, -1, 1)
        scipy.io.wavfile.write(
            RECORDING_PATH,
            SAMPLE_RATE,
            (audio * 32767).astype(np.int16),
        )
        self.record_button.configure(text="recordvoice again", bg=GOLD)
        self.record_status.configure(text=f"Saved as {RECORDING_PATH}", fg=SUCCESS)
        self.submit_button.configure(state="normal")
        self.status.configure(text="Your voice is ready.", fg=SUCCESS)

    def start_clone(self):
        prompt = self.prompt.get("1.0", "end").strip()
        if not prompt:
            messagebox.showwarning("Empty script", "Type something for your voice to say.")
            return
        if not RECORDING_PATH.exists():
            messagebox.showwarning("Record your voice first", "Click recordvoice before submitting text.")
            return

        self.submit_button.configure(state="disabled")
        self.play_button.configure(state="disabled")
        self.record_button.configure(state="disabled")
        self.status.configure(text="Converting text to WAV locally...", fg=GOLD_LIGHT)
        threading.Thread(target=self.run_clone, args=(prompt,), daemon=True).start()

    def run_clone(self, prompt):
        try:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = TTSModel.load_model().to(device)
            if not model.has_voice_cloning:
                raise RuntimeError(
                    "The installed local model does not include custom voice-cloning weights."
                )
            voice_state = model.get_state_for_audio_prompt(RECORDING_PATH)
            audio = model.generate_audio(voice_state, prompt)
            scipy.io.wavfile.write(
                OUTPUT_PATH,
                model.sample_rate,
                audio.detach().cpu().numpy(),
            )
        except Exception as error:
            error_message = str(error)
            self.root.after(0, lambda: self.clone_failed(error_message))
            return
        self.root.after(0, self.clone_finished)

    def clone_failed(self, error_message):
        self.submit_button.configure(state="normal")
        self.record_button.configure(state="normal")
        self.status.configure(text="Rendering could not be completed.", fg=DANGER)
        messagebox.showerror("Voice cloning unavailable", error_message)

    def clone_finished(self):
        self.submit_button.configure(state="normal")
        self.play_button.configure(state="normal")
        self.record_button.configure(state="normal")
        self.output_ready = True
        self.status.configure(text=f"WAV ready: {OUTPUT_PATH}", fg=SUCCESS)
        messagebox.showinfo("WAV ready", f"Your converted audio was saved to {OUTPUT_PATH}.")

    def play_output(self):
        if not self.output_ready or not OUTPUT_PATH.exists():
            messagebox.showwarning("No WAV file", "Submit text first to create the WAV file.")
            return

        try:
            sample_rate, audio = scipy.io.wavfile.read(OUTPUT_PATH)
            sd.stop()
            sd.play(audio, sample_rate)
            self.status.configure(text=f"Playing {OUTPUT_PATH}", fg=GOLD_LIGHT)
        except Exception as error:
            messagebox.showerror("Playback error", str(error))


if __name__ == "__main__":
    window = tk.Tk()
    CloneStudio(window)
    window.mainloop()
