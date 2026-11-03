# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Backend of voice mode: speech synthesis, phrase transcription, the endpoints and their settings."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.api import frontend
from frappe_ai.voice import speech
from frappe_ai.voice.utterance import (
	MAX_UTTERANCE_BYTES,
	read_uploaded_utterance,
	transcribe_utterance,
)

SPEECH_MODEL_TITLE = "Voice Test Speech Model"
STT_MODEL_TITLE = "Voice Test Transcription Model"
SPEECH_CLIENT = "frappe_ai.voice.speech.create_openai_compatible_client"
STT_CLIENT = "frappe_ai.knowledge.transcribe.create_openai_compatible_client"


def _model(title: str, model_id: str, enabled: int = 1) -> str:
	if not frappe.db.exists("AI Provider", "openai"):
		frappe.get_doc(
			{"doctype": "AI Provider", "provider": "openai", "api_key": "k", "enabled": 1}
		).insert()
	name = frappe.db.get_value("AI Model", {"title": title}, "name")
	if name:
		frappe.db.set_value("AI Model", name, "enabled", enabled)
		return name
	doc = {
		"doctype": "AI Model",
		"title": title,
		"provider": "openai",
		"model_id": model_id,
		"enabled": enabled,
	}
	return frappe.get_doc(doc).insert().name


def _set_settings(**values) -> None:
	settings = frappe.get_doc("AI Settings")
	settings.update(values)
	settings.save(ignore_permissions=True)
	frappe.clear_document_cache("AI Settings", "AI Settings")


class _VoiceTestCase(IntegrationTestCase):
	def setUp(self):
		self.speech_model = _model(SPEECH_MODEL_TITLE, "tts-test")
		self.stt_model = _model(STT_MODEL_TITLE, "whisper-test")

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache("AI Settings", "AI Settings")


class TestSpeak(_VoiceTestCase):
	def setUp(self):
		super().setUp()
		_set_settings(speech_model=self.speech_model, speech_voice="nova")

	def _client(self, audio: bytes = b"ID3audio") -> MagicMock:
		client = MagicMock()
		client.audio.speech.create.return_value = SimpleNamespace(content=audio)
		return client

	def test_text_is_sent_to_the_speech_model_and_audio_returned(self):
		client = self._client(b"mp3-bytes")
		with patch(SPEECH_CLIENT, return_value=client) as make_client:
			audio = speech.speak("  Hello there.  ")

		self.assertEqual(audio, b"mp3-bytes")
		call = client.audio.speech.create.call_args.kwargs
		self.assertEqual(call["model"], "tts-test")
		self.assertEqual(call["voice"], "nova")
		self.assertEqual(call["input"], "Hello there.")
		self.assertEqual(call["response_format"], "mp3")
		self.assertEqual(
			make_client.call_args.kwargs, {"timeout": speech.SPEECH_TIMEOUT_SECONDS, "max_retries": 0}
		)

	def test_the_default_voice_is_used_when_none_is_set(self):
		_set_settings(speech_voice="")
		client = self._client()
		with patch(SPEECH_CLIENT, return_value=client):
			speech.speak("Hi.")
		self.assertEqual(client.audio.speech.create.call_args.kwargs["voice"], speech.DEFAULT_VOICE)

	def test_blank_and_oversized_text_is_refused_before_any_request(self):
		with patch(SPEECH_CLIENT) as make_client:
			with self.assertRaisesRegex(frappe.ValidationError, "no text"):
				speech.speak("   ")
			with self.assertRaisesRegex(frappe.ValidationError, "limited to"):
				speech.speak("x" * (speech.MAX_SPEECH_CHARS + 1))
		make_client.assert_not_called()

	def test_it_is_refused_when_no_speech_model_is_configured(self):
		_set_settings(speech_model=None)
		with self.assertRaisesRegex(frappe.ValidationError, "No speech model"):
			speech.speak("Hello.")

	def test_provider_failures_are_reported_without_the_key(self):
		client = MagicMock()
		client.audio.speech.create.side_effect = RuntimeError("boom sk-secret-key-123")
		with patch(SPEECH_CLIENT, return_value=client):
			with self.assertRaises(frappe.ValidationError) as raised:
				speech.speak("Hello.")
		self.assertNotIn("sk-secret-key-123", str(raised.exception))


class TestTranscribeUtterance(_VoiceTestCase):
	def setUp(self):
		super().setUp()
		_set_settings(transcription_model=self.stt_model)

	def _stt_client(self, text: str = "turn on the lights") -> MagicMock:
		client = MagicMock()
		segments = [SimpleNamespace(start=0.0, text=f" {text}")]
		client.audio.transcriptions.create.return_value = SimpleNamespace(text=text, segments=segments)
		return client

	def test_the_phrase_comes_back_as_plain_text_without_timestamps(self):
		with patch(STT_CLIENT, return_value=self._stt_client("turn on the lights")):
			self.assertEqual(transcribe_utterance(b"RIFFdata"), "turn on the lights")

	def test_an_empty_or_oversized_recording_is_refused_before_any_request(self):
		with patch(STT_CLIENT) as make_client:
			with self.assertRaisesRegex(frappe.ValidationError, "empty"):
				transcribe_utterance(b"")
			with self.assertRaisesRegex(frappe.ValidationError, "too long"):
				transcribe_utterance(b"x" * (MAX_UTTERANCE_BYTES + 1))
		make_client.assert_not_called()

	def test_it_is_refused_when_audio_is_not_configured(self):
		_set_settings(transcription_model=None)
		with self.assertRaisesRegex(frappe.ValidationError, "transcription model"):
			transcribe_utterance(b"RIFFdata")


class TestReadUploadedUtterance(IntegrationTestCase):
	@staticmethod
	def _request(content: bytes | None, content_length: int | None = None):
		files = (
			{"file": SimpleNamespace(stream=SimpleNamespace(read=lambda: content))}
			if content is not None
			else {}
		)
		return SimpleNamespace(
			files=files, content_length=content_length if content_length is not None else len(content or b"")
		)

	def test_the_uploaded_bytes_are_returned(self):
		self.assertEqual(read_uploaded_utterance(self._request(b"RIFFdata")), b"RIFFdata")

	def test_a_missing_file_is_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "recording is required"):
			read_uploaded_utterance(self._request(None))

	def test_an_oversized_body_is_refused_without_reading_it(self):
		request = self._request(b"x", content_length=MAX_UTTERANCE_BYTES * 2)
		request.files["file"].stream.read = MagicMock()
		with self.assertRaisesRegex(frappe.ValidationError, "too long"):
			read_uploaded_utterance(request)
		request.files["file"].stream.read.assert_not_called()


class TestVoiceEndpoints(_VoiceTestCase):
	def test_voice_is_unavailable_without_a_transcription_model(self):
		_set_settings(transcription_model=None, speech_model=None)
		self.assertEqual(frontend._voice_capabilities(), {"available": False, "tts": "browser"})

	def test_voice_uses_the_browser_voice_until_a_speech_model_is_set(self):
		_set_settings(transcription_model=self.stt_model, speech_model=None)
		self.assertEqual(frontend._voice_capabilities(), {"available": True, "tts": "browser"})

	def test_voice_uses_the_server_voice_when_a_speech_model_is_set(self):
		_set_settings(transcription_model=self.stt_model, speech_model=self.speech_model)
		self.assertEqual(frontend._voice_capabilities(), {"available": True, "tts": "server"})

	def test_the_bootstrap_payload_carries_the_voice_capabilities(self):
		_set_settings(transcription_model=self.stt_model, speech_model=None)
		self.assertEqual(frontend.bootstrap()["composer"]["voice"], {"available": True, "tts": "browser"})

	def test_the_speak_endpoint_returns_base64_audio(self):
		_set_settings(speech_model=self.speech_model)
		with patch.object(frontend, "speak", return_value=b"mp3-bytes"):
			reply = frontend.speak_text.__wrapped__.__wrapped__("Hello.")
		self.assertEqual(base64.b64decode(reply["audio"]), b"mp3-bytes")
		self.assertEqual(reply["format"], "mp3")

	def test_guests_cannot_use_the_voice_endpoints(self):
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			frontend.speak_text.__wrapped__.__wrapped__("Hello.")
		with self.assertRaises(frappe.PermissionError):
			frontend.transcribe_voice.__wrapped__.__wrapped__()


class TestVoiceSettings(_VoiceTestCase):
	def test_a_disabled_speech_model_cannot_be_selected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Speech Model .* is disabled"):
			_set_settings(speech_model=_model("Voice Disabled Speech", "tts-off", enabled=0))

	def test_the_voice_has_a_default(self):
		self.assertEqual(frappe.get_doc("AI Settings").meta.get_field("speech_voice").default, "alloy")
