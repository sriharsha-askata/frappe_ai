# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Audio transcription: configuration, size limit, transcript format, and the attachment rules."""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from frappe_ai.frappe_ai.doctype.ai_session_attachment.ai_session_attachment import (
	extract_attachment,
	resolve_attachment,
)
from frappe_ai.knowledge import extract, transcribe
from frappe_ai.knowledge.transcribe import AUDIO_EXTENSIONS, format_transcript

MODEL_TITLE = "Transcribe Test Model"
CLIENT_FACTORY = "frappe_ai.knowledge.transcribe.create_openai_compatible_client"


def _set_settings(**values) -> None:
	settings = frappe.get_doc("AI Settings")
	settings.update(values)
	settings.save(ignore_permissions=True)
	frappe.clear_document_cache("AI Settings", "AI Settings")


def _make_model(enabled: int = 1) -> str:
	if not frappe.db.exists("AI Provider", "openai"):
		frappe.get_doc(
			{"doctype": "AI Provider", "provider": "openai", "api_key": "k", "enabled": 1}
		).insert()
	existing = frappe.db.get_value("AI Model", {"title": MODEL_TITLE}, "name")
	if existing:
		frappe.db.set_value("AI Model", existing, "enabled", enabled)
		return existing
	doc = frappe.get_doc(
		{
			"doctype": "AI Model",
			"title": MODEL_TITLE,
			"provider": "openai",
			"model_id": "whisper-test",
			"enabled": enabled,
		}
	)
	return doc.insert().name


def _fake_client(response) -> MagicMock:
	client = MagicMock()
	client.audio.transcriptions.create.return_value = response
	return client


def _verbose_response():
	segments = [
		SimpleNamespace(start=0.0, text=" Hello there."),
		SimpleNamespace(start=3725.4, text=" Goodbye."),
	]
	return SimpleNamespace(text="Hello there. Goodbye.", segments=segments)


class _AudioTestCase(IntegrationTestCase):
	def setUp(self):
		self.model = _make_model()
		_set_settings(transcription_model=self.model, max_audio_mb=25, transcription_timeout=110)

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache("AI Settings", "AI Settings")


class TestTranscriptFormat(IntegrationTestCase):
	def test_segments_become_timestamped_lines(self):
		self.assertEqual(
			format_transcript(_verbose_response()),
			"[00:00:00] Hello there.\n[01:02:05] Goodbye.",
		)

	def test_plain_text_is_used_when_there_are_no_segments(self):
		self.assertEqual(
			format_transcript(SimpleNamespace(text="  just words  ", segments=None)), "just words"
		)

	def test_dict_responses_are_understood(self):
		response = {"text": "x", "segments": [{"start": 65, "text": " Hi."}]}
		self.assertEqual(format_transcript(response), "[00:01:05] Hi.")

	def test_empty_segments_are_skipped(self):
		response = SimpleNamespace(text="Kept.", segments=[SimpleNamespace(start=0, text="  ")])
		self.assertEqual(format_transcript(response), "Kept.")


class TestTranscription(_AudioTestCase):
	def test_audio_is_sent_to_the_configured_model_and_formatted(self):
		client = _fake_client(_verbose_response())
		with patch(CLIENT_FACTORY, return_value=client) as make_client:
			text = transcribe.transcribe("audio.wav", b"RIFFdata")

		self.assertIn("[00:00:00] Hello there.", text)
		call = client.audio.transcriptions.create.call_args.kwargs
		self.assertEqual(call["model"], "whisper-test")
		self.assertEqual(call["file"], ("audio.wav", b"RIFFdata"))
		self.assertEqual(call["response_format"], "verbose_json")
		self.assertEqual(make_client.call_args.kwargs, {"timeout": 110, "max_retries": 0})

	def test_a_file_on_disk_is_streamed_not_loaded(self):
		client = _fake_client(_verbose_response())
		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
			handle.write(b"ID3audio")
		self.addCleanup(os.remove, handle.name)
		with patch(CLIENT_FACTORY, return_value=client):
			transcribe.transcribe_file(handle.name, "mp3")

		filename, opened = client.audio.transcriptions.create.call_args.kwargs["file"]
		self.assertEqual(filename, "audio.mp3")
		self.assertTrue(hasattr(opened, "read"))

	def test_a_file_over_the_limit_is_refused_before_any_request(self):
		_set_settings(max_audio_mb=1)
		with patch(CLIENT_FACTORY) as make_client:
			with self.assertRaisesRegex(frappe.ValidationError, "limit is 1 MB"):
				transcribe.transcribe("audio.wav", b"x" * (2 * 1024 * 1024))
		make_client.assert_not_called()

	def test_provider_failures_are_reported_without_the_key(self):
		client = MagicMock()
		client.audio.transcriptions.create.side_effect = RuntimeError("boom sk-secret-key-123")
		with patch(CLIENT_FACTORY, return_value=client):
			with self.assertRaises(frappe.ValidationError) as raised:
				transcribe.transcribe("audio.wav", b"data")
		self.assertNotIn("sk-secret-key-123", str(raised.exception))


class TestAudioSwitch(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache("AI Settings", "AI Settings")

	def test_audio_is_off_until_a_model_is_configured(self):
		_set_settings(transcription_model=None)
		self.assertFalse(AUDIO_EXTENSIONS & extract.supported_extensions())
		with self.assertRaisesRegex(frappe.ValidationError, "transcription model"):
			transcribe.require_transcription_model()

	def test_audio_types_are_offered_once_a_model_is_configured(self):
		_set_settings(transcription_model=_make_model())
		self.assertTrue(AUDIO_EXTENSIONS <= extract.supported_extensions())
		self.assertTrue(extract.FILE_EXTENSIONS <= extract.supported_extensions())

	def test_a_disabled_model_cannot_be_selected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "disabled"):
			_set_settings(transcription_model=_make_model(enabled=0))

	def test_the_limits_cannot_be_negative(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Max Audio Size"):
			_set_settings(max_audio_mb=-1)
		with self.assertRaisesRegex(frappe.ValidationError, "Timeout"):
			_set_settings(transcription_timeout=-1)

	def test_a_blank_limit_falls_back_to_the_default(self):
		_set_settings(max_audio_mb=0, transcription_timeout=0)
		transcribe.check_audio_size(
			transcribe.DEFAULT_MAX_AUDIO_MB * transcribe.BYTES_PER_MB
		)  # at the limit: fine
		with self.assertRaisesRegex(frappe.ValidationError, "limit is 25 MB"):
			transcribe.check_audio_size(transcribe.DEFAULT_MAX_AUDIO_MB * transcribe.BYTES_PER_MB + 1)


class TestAudioAttachments(_AudioTestCase):
	def _audio_file(self, name: str = "call.wav", content: bytes = b"RIFFdata"):
		return frappe.get_doc(
			{"doctype": "File", "file_name": name, "content": content, "is_private": 1}
		).insert(ignore_permissions=True)

	def test_an_audio_attachment_is_transcribed_at_upload(self):
		file_doc = self._audio_file()
		with patch(CLIENT_FACTORY, return_value=_fake_client(_verbose_response())):
			data = extract_attachment(file_doc.name)
		self.assertIn("Hello there.", data["extracted_text"])

	def test_audio_is_rejected_with_a_clear_message_when_switched_off(self):
		file_doc = self._audio_file()
		_set_settings(transcription_model=None)
		with self.assertRaisesRegex(frappe.ValidationError, "transcription model"):
			extract_attachment(file_doc.name)

	def test_an_oversized_audio_file_is_refused_without_reading_it(self):
		file_doc = self._audio_file(content=b"x" * (2 * 1024 * 1024))
		_set_settings(max_audio_mb=1)
		with patch(CLIENT_FACTORY) as make_client:
			with self.assertRaisesRegex(frappe.ValidationError, "limit is 1 MB"):
				extract_attachment(file_doc.name)
		make_client.assert_not_called()

	def test_an_expired_audio_upload_asks_to_attach_again_instead_of_retranscribing(self):
		file_doc = self._audio_file()
		with patch(CLIENT_FACTORY) as make_client:
			with self.assertRaisesRegex(frappe.ValidationError, "Attach the file again"):
				resolve_attachment(file_doc.name)
		make_client.assert_not_called()

	def test_a_staged_audio_attachment_is_reused(self):
		file_doc = self._audio_file()
		staged = {"file": file_doc.name, "extracted_text": "already transcribed"}
		with patch(
			"frappe_ai.frappe_ai.doctype.ai_session_attachment.ai_session_attachment.staged_attachment",
			return_value=staged,
		):
			self.assertEqual(resolve_attachment(file_doc.name), staged)

	def test_text_attachments_still_re_extract_after_the_cache_expires(self):
		file_doc = self._audio_file(name="notes.txt", content=b"plain notes")
		self.assertEqual(resolve_attachment(file_doc.name)["extracted_text"], "plain notes")
