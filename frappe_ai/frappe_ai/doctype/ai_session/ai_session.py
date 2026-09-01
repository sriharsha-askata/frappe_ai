# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""`AI Session` — ported from `flow`'s `Flow Session`
(see `apps/flow/flow/flow/doctype/flow_session/flow_session.py`).

`flow`'s `FlowSession.chat()`/`.resume()`/`_runtime`/`new_session()`/`load_session()`
have **no equivalent here** — those drove flow's in-process Agent runtime, which
`frappe_ai` replaces entirely with the FastAPI service + `AgentBuilder` +
`frappe_ai.api.dispatch`/`frappe_ai.api.api.start_run`/`resume_run`. This controller
keeps only the config/persistence surface flow's DocType itself owned: agent-locking,
model-enabled validation, the owner chokepoint, transcript append, log clearing, and
title derivation.

**Phase 5 update:** `build_prompt_messages()` now also injects an `<agent_memory>` block
into the stored system message when the agent has durable memories.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_ai.lib.model import CHARS_PER_TOKEN

TITLE_MAX_LENGTH = 80
# A "Running" run older than this is treated as abandoned and no longer blocks the session.
RUNNING_STALE_SECONDS = 300

# Attachment routing thresholds — ported from flow. A file whose text exceeds
# RETRIEVAL_FRACTION of the model's context window is too big to inline every turn,
# so it is chunked and retrieved instead when the fixed embedding integration can
# serve it; failures demote it to Inline and it is truncated to fit at prompt-build time.
DEFAULT_CONTEXT_WINDOW = 128000
RETRIEVAL_FRACTION = 0.5
RESERVED_OUTPUT_TOKENS = 4096
# Top-K retrieval chunks injected for the latest user turn's retrieval-mode attachments.
RETRIEVAL_TOP_K = 8


class AISession(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from frappe_ai.frappe_ai.doctype.ai_session_attachment.ai_session_attachment import (
			AISessionAttachment,
		)
		from frappe_ai.frappe_ai.doctype.ai_session_message.ai_session_message import AISessionMessage

		agent: DF.Link | None
		attachments: DF.Table[AISessionAttachment]
		messages: DF.Table[AISessionMessage]
		model: DF.Link | None
		source: DF.Literal["Manual", "Trigger"]
		title: DF.Data | None
	# end: auto-generated types

	def validate(self):
		self._validate_agent_unchanged()
		self._validate_model_enabled()

	def on_trash(self):
		frappe.db.delete("AI Run", {"session": self.name})
		_purge_attachment_chunks(self.name)

	def _validate_model_enabled(self):
		if not self.model:
			return
		if not frappe.db.get_value("AI Model", self.model, "enabled"):
			frappe.throw(
				_("AI Model {0} is disabled.").format(self.model),
				title=_("Disabled Model"),
			)

	def _validate_agent_unchanged(self):
		"""The agent that drives a session is fixed at creation. Subsequent turns must use the same agent."""
		if self.is_new():
			return
		db_agent = frappe.db.get_value("AI Session", self.name, "agent")
		if (db_agent or None) != (self.agent or None):
			frappe.throw(
				_("Cannot change the agent on an existing session."),
				title=_("Agent Locked"),
			)

	@staticmethod
	def clear_old_logs(days=30):
		"""Delete sessions idle for `days`, along with their AI Runs and transcript rows.
		Age is last activity (modified), so an actively-used session is never purged."""
		cutoff = frappe.utils.add_days(frappe.utils.now(), -days)
		sessions = frappe.get_all("AI Session", filters={"modified": ["<", cutoff]}, pluck="name")
		for batch in frappe.utils.create_batch(sessions, 100):
			frappe.db.delete("AI Run", {"session": ["in", batch]})
			frappe.db.delete("AI Session Message", {"parent": ["in", batch]})
			_delete_attachment_files(batch)
			frappe.db.delete("AI Session Attachment", {"parent": ["in", batch]})
			frappe.db.delete("AI Session", {"name": ["in", batch]})
			_purge_attachment_chunks(batch)

	def transcript(self) -> list[dict[str, Any]]:
		"""Return the conversation history in OpenAI message format."""
		return [_row_to_message(row) for row in self.messages]

	def append_run_messages(self, new_messages: list[dict[str, Any]], run: str) -> None:
		"""Append the messages produced by `run` to this session's transcript.

		`new_messages` should be the delta — only messages this run added — not the
		cumulative history. Each row is tagged with the producing run for traceability.
		"""
		for message in new_messages:
			role = message.get("role")
			tool_calls = message.get("tool_calls")
			self.append(
				"messages",
				{
					"role": role,
					"content": message.get("content"),
					"tool_call_id": message.get("tool_call_id"),
					"tool_calls": json.dumps(tool_calls) if tool_calls else None,
					"run": run,
				},
			)
		self.save(ignore_permissions=True)

	def persist_turn(
		self, input: str, instructions: str | None, attachment_data: list[dict[str, Any]], run: str
	) -> None:
		"""Persist this turn's user message (and the system message on the first turn) plus
		its attachment rows before the run executes. Called by `frappe_ai.api.api.start_run`
		before handing off to the FastAPI service.

		Args:
			input (str): The user's message text.
			instructions (str | None): The agent's system instructions — stored only on
				the session's first turn.
			attachment_data (list[dict[str, Any]]): Resolved attachments
				(`AISessionAttachment.resolve_attachment` output), one per file.
			run (str): The `AI Run` name this turn belongs to.
		"""
		if not self.messages and instructions:
			self.append("messages", {"role": "system", "content": instructions, "run": run})
		self.append("messages", {"role": "user", "content": input, "run": run})

		threshold = self._attachment_inline_threshold()
		embeddings_on = _embeddings_configured()
		for data in attachment_data:
			text = data["extracted_text"]
			mode = _route_attachment(text, threshold, embeddings_on)
			self.append(
				"attachments",
				{
					"file": data["file"],
					"file_name": data["file_name"],
					"file_size": data["file_size"],
					"extracted_text": text[:threshold],
					"mode": mode,
					"run": run,
				},
			)
		self.save(ignore_permissions=True)
		self._index_retrieval_attachments(run, {d["file"]: d["extracted_text"] for d in attachment_data})

	def _context_window(self, snapshot: dict[str, Any] | None = None) -> int:
		"""The effective model's context window in tokens (a default when unknown)."""
		model = (snapshot or {}).get("model")
		return (model and frappe.db.get_value("AI Model", model, "context_window")) or DEFAULT_CONTEXT_WINDOW

	def _attachment_inline_threshold(self, snapshot: dict[str, Any] | None = None) -> int:
		"""Max characters of file text to inline before routing to retrieval."""
		return int(self._context_window(snapshot) * CHARS_PER_TOKEN * RETRIEVAL_FRACTION)

	def _index_retrieval_attachments(self, run: str, texts: dict[str, str] | None = None) -> None:
		"""Chunk, embed, and store this run's retrieval-mode attachments, preferring the full
		in-memory `texts` over the (capped) row text. Demotes to Inline on failure."""
		rows = [a for a in self.attachments if a.run == run and a.mode == "Retrieval"]
		if not rows:
			return

		from frappe.utils import cint

		from frappe_ai.knowledge import attachment_store
		from frappe_ai.knowledge.chunker import chunk_text
		from frappe_ai.knowledge.embedder import embed_texts

		texts = texts or {}
		settings = frappe.get_cached_doc("AI Settings")
		for row in rows:
			try:
				chunks = chunk_text(
					texts.get(row.file) or row.extracted_text,
					chunk_size=cint(settings.chunk_size),
					overlap=cint(settings.chunk_overlap),
				)
				if not chunks:
					row.db_set("mode", "Inline")
					row.mode = "Inline"
					continue
				vectors = embed_texts(chunks)
				attachment_store.ensure_table(len(vectors[0]))
				attachment_store.add(self.name, row.name, chunks, vectors)
			except Exception:
				# Permanent demotion: data never made it to LanceDB, so Retrieval mode
				# would silently return nothing. Inline ensures the user gets the capped text.
				frappe.log_error(title="Chat attachment indexing failed")
				row.db_set("mode", "Inline")
				row.mode = "Inline"

	def _file_injection_budget(self) -> int:
		"""Characters left for file content this turn: the context window minus the reply
		reservation and the conversation text already in the transcript."""
		window_chars = self._context_window() * CHARS_PER_TOKEN
		reserved = RESERVED_OUTPUT_TOKENS * CHARS_PER_TOKEN
		dialogue = sum(len(m.content or "") for m in self.messages)
		return max(0, window_chars - reserved - dialogue)

	def build_prompt_messages(self) -> list[dict[str, Any]]:
		"""Transcript as sent to the model. Augmentation is ephemeral — stored messages stay
		clean (file text lives only in the attachments child table):

		- Inline files: full text re-injected on their turn, clamped to the remaining budget.
		- Retrieval files: a short note marks where each was attached; for the latest user
		  turn the most relevant chunks (by that turn's query) are injected in place of the
		  full text.

		"""
		from frappe_ai.knowledge.embedder import EmbeddingServiceUnavailable
		from frappe_ai.knowledge.retriever import retrieve_attachments
		from frappe_ai.memory.memory import build_memory_block

		last_user_run = self._latest_user_run()
		query = self._latest_user_content() if any(a.mode == "Retrieval" for a in self.attachments) else None
		retrieved_chunks: list[dict[str, Any]] = []
		if query:
			try:
				retrieved_chunks = retrieve_attachments(query, session=self.name, limit=RETRIEVAL_TOP_K)
			except (EmbeddingServiceUnavailable, frappe.ValidationError):
				self._demote_retrieval_attachments()
				query = None

		attachments_by_run = self._group_attachments_by_run()
		budget = self._file_injection_budget()

		messages: list[dict[str, Any]] = []
		for row in self.messages:
			message = _row_to_message(row)
			if row.role == "system" and self.agent:
				memory_block = build_memory_block(self.agent, query=self._latest_user_content())
				if memory_block:
					message["content"] = f"{message['content']}\n\n{memory_block}" if message["content"] else memory_block
			if row.role == "user":
				content = message["content"]
				attachments = attachments_by_run.get(row.run, [])
				inline = [a for a in attachments if a.mode == "Inline"]
				retrieval = [a for a in attachments if a.mode == "Retrieval"]
				if inline:
					content, budget = _inject_inline_files(content, inline, budget)
				if retrieval:
					content = _note_retrieval_files(content, retrieval)
				if query and row.run == last_user_run:
					content, budget = _inject_retrieved_chunks(content, retrieved_chunks, budget)
				message["content"] = content
			messages.append(message)
		return messages

	def _demote_retrieval_attachments(self) -> None:
		"""Temporarily fall back to inline when embedding is unavailable.

		In-memory only — DB retains Retrieval so the next request retries automatically
		when the embedding service recovers.
		"""
		for row in self.attachments:
			if row.mode != "Retrieval":
				continue
			row.mode = "Inline"

	def _latest_user_run(self) -> str | None:
		for row in reversed(self.messages):
			if row.role == "user":
				return row.run
		return None

	def _latest_user_content(self) -> str:
		for row in reversed(self.messages):
			if row.role == "user":
				return row.content or ""
		return ""

	def _group_attachments_by_run(self) -> dict[str, list[Any]]:
		grouped: dict[str, list[Any]] = {}
		for attachment in self.attachments:
			grouped.setdefault(attachment.run, []).append(attachment)
		return grouped

	def assert_not_blocked(self) -> None:
		"""Refuse a new turn while a run is Paused or genuinely Running. A stale
		Running run (crashed stream, no callback ever arrived) is auto-failed instead
		of blocking forever."""
		blocking = frappe.db.get_value(
			"AI Run",
			{"session": self.name, "status": ("in", ["Paused", "Running"])},
			["name", "status", "creation"],
			order_by="creation desc",
			as_dict=True,
		)
		if not blocking:
			return
		if blocking.status == "Paused":
			frappe.throw(
				_("This session has a paused run. Resume it before starting a new turn."),
				title=_("Run Paused"),
			)
		age = frappe.utils.time_diff_in_seconds(frappe.utils.now_datetime(), blocking.creation)
		if age > RUNNING_STALE_SECONDS:
			frappe.db.set_value(
				"AI Run",
				blocking.name,
				{"status": "Failed", "error": "Run abandoned: stream ended without completing."},
			)
			return
		frappe.throw(
			_("This session already has a run in progress."),
			title=_("Run In Progress"),
		)


def assert_session_owner(session) -> None:
	"""Owner match or `write` permission, else `frappe.PermissionError`.

	Authorization chokepoint: `AISession.append_run_messages`/`persist_turn` use
	`ignore_permissions=True`, so this check is the only thing standing between a user
	and someone else's conversation.

	Args:
		session: An `AI Session` name (str) or already-loaded document.
	"""
	doc = frappe.get_doc("AI Session", session) if isinstance(session, str) else session
	if doc.owner == frappe.session.user:
		return
	if frappe.has_permission("AI Session", "write", doc):
		return
	frappe.throw(_("Not permitted to use this session."), frappe.PermissionError)


def derive_title(text: str) -> str:
	"""Pick a short title from a user message. Single line, capped at TITLE_MAX_LENGTH."""
	cleaned = " ".join((text or "").split())
	if len(cleaned) <= TITLE_MAX_LENGTH:
		return cleaned
	return cleaned[: TITLE_MAX_LENGTH - 1].rstrip() + "…"


def _delete_attachment_files(sessions: list[str]) -> None:
	"""Delete the uploaded File docs attached to these sessions. Per-doc (not bulk SQL)
	so File.on_trash runs to remove the on-disk content, and best-effort so one failure
	never aborts the purge."""
	files = frappe.get_all("AI Session Attachment", filters={"parent": ["in", sessions]}, pluck="file")
	for file in set(filter(None, files)):
		try:
			frappe.delete_doc("File", file, ignore_permissions=True, force=True, delete_permanently=True)
		except Exception:
			frappe.log_error(title="Chat attachment file cleanup failed")


def _purge_attachment_chunks(session: str | list[str]) -> None:
	"""Best-effort removal of a session's (or batch's) retrieval chunks. The chunk index is
	disposable, so a failure here must never block deleting the session."""
	from frappe_ai.knowledge import attachment_store

	try:
		attachment_store.delete(session=session)
	except Exception:
		frappe.log_error(title="Chat attachment cleanup failed")


def _embeddings_configured() -> bool:
	"""Whether the fixed embedding integration is configured.

	Availability is intentionally not probed here. A real request during indexing
	(or retrieval) is the availability check, and failures are handled at those
	boundaries so ordinary chat can continue.
	"""
	return True


def _route_attachment(text: str | None, threshold: int, embeddings_on: bool) -> str:
	"""Inline a file that fits; route an oversized one to retrieval when embeddings are
	available, else keep it Inline (it is truncated to fit at prompt-build time)."""
	if len(text or "") <= threshold:
		return "Inline"
	return "Retrieval" if embeddings_on else "Inline"


def _row_to_message(row) -> dict[str, Any]:
	"""Convert a stored transcript row to an OpenAI-format message dict."""
	if row.role == "tool":
		return {"role": "tool", "tool_call_id": row.tool_call_id, "content": row.content or ""}
	message: dict[str, Any] = {"role": row.role, "content": row.content}
	if row.tool_calls:
		message["tool_calls"] = json.loads(row.tool_calls)
	return message


def _inject_inline_files(content: str | None, attachments: list[Any], budget: int) -> tuple[str, int]:
	"""Append inline files' full text to a user message, clamped to the shared `budget`.
	Returns the augmented content and the remaining budget."""
	blocks = []
	for a in attachments:
		text, truncated = _clamp(a.extracted_text or "", budget)
		budget -= len(text)
		marker = _("\n\n[File truncated to fit the context window.]") if truncated else ""
		blocks.append(f"--- File: {a.file_name} ---\n{text}{marker}\n--- End of file: {a.file_name} ---")
	body = f"{_('The user attached the following file(s):')}\n\n" + "\n\n".join(blocks)
	return (f"{content}\n\n{body}" if content else body), budget


def _note_retrieval_files(content: str | None, attachments: list[Any]) -> str:
	"""Mark where large (retrieval-mode) files were attached, without their bulk. Their
	relevant excerpts are injected on the latest turn rather than inline here."""
	names = ", ".join(a.file_name for a in attachments)
	note = _("The user attached file(s) (large; relevant excerpts shown below): {0}").format(names)
	return f"{content}\n\n{note}" if content else note


def _inject_retrieved_chunks(
	content: str | None, chunks: list[dict[str, Any]], budget: int
) -> tuple[str, int]:
	"""Append retrieved excerpts to the latest user message, within the remaining budget."""
	blocks = []
	for chunk in chunks:
		text, _truncated = _clamp(chunk["content"], budget)
		if not text:
			break
		budget -= len(text)
		blocks.append(text)
	if not blocks:
		return content or "", budget
	body = f"{_('Relevant excerpts from the attached file(s):')}\n\n" + "\n\n---\n\n".join(blocks)
	return (f"{content}\n\n{body}" if content else body), budget


def _clamp(text: str, limit: int) -> tuple[str, bool]:
	"""Return (text capped at `limit` chars, was_truncated)."""
	if limit <= 0:
		return "", bool(text)
	if len(text) <= limit:
		return text, False
	return text[:limit], True
