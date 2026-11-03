# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Turn an audio file into text with an OpenAI-compatible speech-to-text endpoint.

The model that does the transcribing is chosen in `AI Settings.transcription_model` and reuses the
credentials of its `AI Provider`. Audio is sent to that provider, so the feature is off until an
administrator selects a model. The transcript then travels through the same attachment and knowledge
paths as any other extracted text.
"""

from __future__ import annotations

import os
from typing import Any, BinaryIO

import frappe
from frappe import _
from frappe.utils import cint

from frappe_ai.lib.model import (
	create_openai_compatible_client,
	normalize_provider_error,
	resolve_model_config,
)

#: Containers the OpenAI transcription API accepts. `mp4`, `webm` and `ogg` may carry no audio.
AUDIO_EXTENSIONS = frozenset({"mp3", "wav", "m4a", "ogg", "flac", "webm", "mp4", "mpeg", "mpga"})

BYTES_PER_MB = 1024 * 1024
#: Used when a setting is blank or 0, which is what a site upgraded before these fields existed holds.
DEFAULT_MAX_AUDIO_MB = 25
DEFAULT_TIMEOUT_SECONDS = 110
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


def is_audio_extension(extension: str) -> bool:
	return extension.lower().lstrip(".") in AUDIO_EXTENSIONS


def transcription_model() -> str | None:
	"""Name of the configured transcription `AI Model`, or None when audio is switched off."""
	return frappe.get_cached_doc("AI Settings").get("transcription_model") or None


def require_transcription_model() -> str:
	"""The configured model name, or an error that tells the user how to enable audio."""
	model = transcription_model()
	if not model:
		frappe.throw(
			_("Audio needs a transcription model. Set one in AI Settings."),
			title=_("Audio Not Enabled"),
		)
	return model


def check_audio_size(size_bytes: int) -> None:
	"""Refuse a file over `AI Settings.max_audio_mb` before anything is read or sent."""
	limit_mb = cint(frappe.get_cached_doc("AI Settings").max_audio_mb) or DEFAULT_MAX_AUDIO_MB
	if size_bytes > limit_mb * BYTES_PER_MB:
		frappe.throw(
			_("This audio file is {0} MB; the limit is {1} MB.").format(
				round(size_bytes / BYTES_PER_MB, 1), limit_mb
			),
			title=_("Audio Too Large"),
		)


def transcribe(filename: str, content: bytes) -> str:
	"""Transcribe audio held in memory. `filename` must keep the real extension: the provider
	uses it to detect the format."""
	return _transcribe(filename, content, len(content))


def transcribe_file(path: str, extension: str) -> str:
	"""Transcribe an audio file on disk without reading it into memory first."""
	size = os.path.getsize(path)
	with open(path, "rb") as handle:
		return _transcribe(f"audio.{extension}", handle, size)


def _transcribe(filename: str, audio: bytes | BinaryIO, size_bytes: int) -> str:
	model_name = require_transcription_model()
	check_audio_size(size_bytes)

	settings = frappe.get_cached_doc("AI Settings")
	config = resolve_model_config(frappe.get_doc("AI Model", model_name))
	timeout = cint(settings.transcription_timeout) or DEFAULT_TIMEOUT_SECONDS
	client = create_openai_compatible_client(config, timeout=timeout, max_retries=0)
	try:
		response = client.audio.transcriptions.create(
			model=config["model_id"], file=(filename, audio), response_format="verbose_json"
		)
	except Exception as error:
		failure = normalize_provider_error(error, provider=config["provider"], model_id=config["model_id"])
		frappe.throw(failure.message, title=_("Transcription Failed"))
	return format_transcript(response)


def format_transcript(response: Any) -> str:
	"""One `[hh:mm:ss] text` line per segment; the plain text when the provider sends no segments."""
	segments = _field(response, "segments") or []
	lines = [_segment_line(segment) for segment in segments if (_field(segment, "text") or "").strip()]
	if lines:
		return "\n".join(lines)
	return (_field(response, "text") or "").strip()


def _segment_line(segment: Any) -> str:
	start = _field(segment, "start") or 0
	return f"[{_timestamp(start)}] {_field(segment, 'text').strip()}"


def _timestamp(seconds: float) -> str:
	whole = int(seconds)
	hours, remainder = divmod(whole, SECONDS_PER_HOUR)
	minutes, secs = divmod(remainder, SECONDS_PER_MINUTE)
	return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _field(item: Any, name: str) -> Any:
	"""Read `name` from an SDK object or a plain dict; providers differ in what they return."""
	if isinstance(item, dict):
		return item.get(name)
	return getattr(item, name, None)
