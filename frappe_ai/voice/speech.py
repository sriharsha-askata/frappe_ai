# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Turn reply text into spoken audio with an OpenAI-compatible text-to-speech endpoint.

The model is chosen in `AI Settings.speech_model` and reuses the credentials of its `AI Provider`.
The text is sent to that provider. With no model configured the browser speaks the reply itself,
so voice mode works without this module.
"""

from __future__ import annotations

import frappe
from frappe import _

from frappe_ai.lib.model import (
	create_openai_compatible_client,
	normalize_provider_error,
	resolve_model_config,
)

#: Replies are spoken a sentence at a time, so a longer text means something is wrong.
MAX_SPEECH_CHARS = 1000
SPEECH_TIMEOUT_SECONDS = 30
DEFAULT_VOICE = "alloy"
#: mp3 is small and plays in every browser; wav would be roughly ten times larger.
AUDIO_FORMAT = "mp3"


def speech_model() -> str | None:
	"""Name of the configured speech `AI Model`, or None when the browser should speak."""
	return frappe.get_cached_doc("AI Settings").get("speech_model") or None


def speak(text: str) -> bytes:
	"""Synthesize `text` with the configured speech model and return the audio bytes."""
	text = _checked_text(text)
	model_name = speech_model()
	if not model_name:
		frappe.throw(
			_("No speech model is configured. Set one in AI Settings."), title=_("Speech Not Enabled")
		)

	config = resolve_model_config(frappe.get_doc("AI Model", model_name))
	client = create_openai_compatible_client(config, timeout=SPEECH_TIMEOUT_SECONDS, max_retries=0)
	voice = frappe.get_cached_doc("AI Settings").get("speech_voice") or DEFAULT_VOICE
	try:
		response = client.audio.speech.create(
			model=config["model_id"], voice=voice, input=text, response_format=AUDIO_FORMAT
		)
	except Exception as error:
		failure = normalize_provider_error(error, provider=config["provider"], model_id=config["model_id"])
		frappe.throw(failure.message, title=_("Speech Failed"))
	return response.content


def _checked_text(text: str) -> str:
	text = (text or "").strip()
	if not text:
		frappe.throw(_("There is no text to speak."), title=_("Nothing To Speak"))
	if len(text) > MAX_SPEECH_CHARS:
		frappe.throw(
			_("Text to speak is limited to {0} characters.").format(MAX_SPEECH_CHARS),
			title=_("Text Too Long"),
		)
	return text
