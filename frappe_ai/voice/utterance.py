# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Turn one spoken utterance from the microphone into text.

Voice mode records each phrase in the browser and sends it here. Nothing is stored: the audio goes
to the transcription model and the text comes back. The text is then handled exactly like typed input.
"""

from __future__ import annotations

import frappe
from frappe import _

from frappe_ai.knowledge.transcribe import require_transcription_model, transcribe

#: A phrase is a few seconds; 16 kHz mono WAV is about 32 KB/s, so this allows well over a minute.
MAX_UTTERANCE_BYTES = 5 * 1024 * 1024
UTTERANCE_FILENAME = "utterance.wav"


def transcribe_utterance(audio: bytes) -> str:
	"""Plain text of a recorded phrase (WAV bytes), without segment timestamps."""
	require_transcription_model()
	if not audio:
		frappe.throw(_("The recording is empty."), title=_("Empty Recording"))
	if len(audio) > MAX_UTTERANCE_BYTES:
		frappe.throw(_("The recording is too long."), title=_("Recording Too Long"))
	return transcribe(UTTERANCE_FILENAME, audio, timestamps=False).strip()


def read_uploaded_utterance(request) -> bytes:
	"""The `file` part of a multipart upload, refusing an oversized body before reading it."""
	if "file" not in request.files:
		frappe.throw(_("A recording is required."), title=_("Invalid Recording"))
	if (request.content_length or 0) > MAX_UTTERANCE_BYTES + 1024:  # a little room for multipart framing
		frappe.throw(_("The recording is too long."), title=_("Recording Too Long"))
	return request.files["file"].stream.read()
