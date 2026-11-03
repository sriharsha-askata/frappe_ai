# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Build small WAV files whose bytes carry their own transcript, for the stub transcriber.

`openai_stub.py` reads the text after the marker `SPOKEN:` (ended by a NUL byte) out of the audio and
returns it as the transcript, so tests need no speech model.
"""

from __future__ import annotations

import io
import wave

SAMPLE_RATE_HZ = 8000
SAMPLE_WIDTH_BYTES = 2


def make_wav(spoken: str, *, padding_bytes: int = 0) -> bytes:
	"""A valid mono WAV file that "says" `spoken`; `padding_bytes` of silence make it larger."""
	payload = b"SPOKEN:" + spoken.encode("utf-8") + b"\x00" + b"\x00" * padding_bytes
	buffer = io.BytesIO()
	with wave.open(buffer, "wb") as wav:
		wav.setnchannels(1)
		wav.setsampwidth(SAMPLE_WIDTH_BYTES)
		wav.setframerate(SAMPLE_RATE_HZ)
		wav.writeframes(payload + b"\x00" * (len(payload) % SAMPLE_WIDTH_BYTES))
	return buffer.getvalue()
