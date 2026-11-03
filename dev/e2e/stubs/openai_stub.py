#!/usr/bin/env python3
# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""A small OpenAI-compatible server for end-to-end tests. Standard library only.

It replaces the two external services the app calls so that tests are deterministic:

* ``POST /v1/embeddings`` - hashed bag-of-words vectors (768 wide, L2-normalised). Texts that
  share words are close, so retrieval ranking is meaningful and repeatable. This proves the
  plumbing, not the quality of a real embedding model.
* ``POST /v1/chat/completions`` - a scripted "model". The latest user message selects the behaviour:

  ``[[search: <query>]]``      call the ``search_knowledge`` tool with that query
  ``[[tool: <name> <json>]]``  call any tool with the given JSON arguments
  ``[[fail]]``                 answer with HTTP 500 (provider error)
  anything else                plain text answer

* ``POST /v1/audio/transcriptions`` - a "transcriber" for audio built by the tests. The audio bytes carry
  their own transcript after the marker ``SPOKEN:`` (ended by a NUL byte), so the stub needs no speech model;
  sentences become verbose_json segments five seconds apart. Audio containing ``FAIL`` gets an HTTP 500.

  After a tool result the model answers with ``TOOL RESULT: <first 300 chars>``. Streaming
  (server-sent events) and non-streaming requests are both supported.

Every request body is appended to a JSON-lines log (``--log``) and ``GET /_requests`` returns
them, so a test can assert what the model was sent (for example the system prompt).
"""

from __future__ import annotations

import argparse
import hashlib
from email.parser import BytesParser
from email.policy import default as email_policy
import json
import math
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMENSION = 768
_WORD = re.compile(r"\w+", re.UNICODE)
_SPOKEN = re.compile(rb"SPOKEN:(.*?)\x00", re.S)
SEGMENT_SECONDS = 5.0
_DIRECTIVE = re.compile(r"\[\[(search|tool|fail)(?::\s*|\s+)?(.*?)\]\]", re.S)

LOG_PATH: str | None = None
CHUNK_DELAY = 0.0
LOG_LOCK = threading.Lock()
REQUESTS: list[dict] = []


def embed(text: str, dimension: int = DIMENSION) -> list[float]:
	vector = [0.0] * dimension
	for token in _WORD.findall((text or "").lower()):
		bucket = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:8], 16) % dimension
		vector[bucket] += 1.0
	norm = math.sqrt(sum(v * v for v in vector)) or 1.0
	return [v / norm for v in vector]


def _text_of(content) -> str:
	if isinstance(content, list):
		return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
	return content or ""


def decide(messages: list[dict]) -> dict:
	"""Return {"text": ...} or {"tool": name, "arguments": {...}} or {"fail": True}."""
	last = messages[-1] if messages else {}
	if last.get("role") == "tool":
		return {"text": "TOOL RESULT: " + _text_of(last.get("content"))[:300]}
	user = next((m for m in reversed(messages) if m.get("role") == "user"), {})
	match = _DIRECTIVE.search(_text_of(user.get("content")))
	if not match:
		return {"text": "OK: " + _text_of(user.get("content"))[:120]}
	kind, rest = match.group(1), match.group(2).strip()
	if kind == "fail":
		return {"fail": True}
	if kind == "search":
		return {"tool": "search_knowledge", "arguments": {"query": rest}}
	name, _, raw = rest.partition(" ")
	return {"tool": name, "arguments": json.loads(raw) if raw.strip() else {}}


def transcribe(audio: bytes) -> dict:
	"""A verbose_json transcription of `audio`, taken from the text embedded in its bytes."""
	match = _SPOKEN.search(audio)
	spoken = match.group(1).decode("utf-8", "replace") if match else f"stub transcript of {len(audio)} bytes"
	sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", spoken) if part.strip()]
	segments = [
		{"id": i, "start": i * SEGMENT_SECONDS, "end": (i + 1) * SEGMENT_SECONDS, "text": " " + sentence}
		for i, sentence in enumerate(sentences)
	]
	return {
		"task": "transcribe",
		"language": "english",
		"duration": len(sentences) * SEGMENT_SECONDS,
		"text": spoken,
		"segments": segments,
	}


def parse_multipart(content_type: str, raw: bytes) -> dict:
	"""Form fields and uploaded files of a multipart body: `{name: str | (filename, bytes)}`."""
	message = BytesParser(policy=email_policy).parsebytes(
		b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + raw
	)
	parts = {}
	for part in message.iter_parts():
		name, filename, payload = (
			part.get_param("name", header="content-disposition"),
			part.get_filename(),
			part.get_payload(decode=True),
		)
		parts[name] = (filename, payload) if filename else payload.decode()
	return parts


def _chunk(model: str, delta: dict, finish=None, usage=None) -> dict:
	body = {
		"id": "chatcmpl-stub",
		"object": "chat.completion.chunk",
		"created": int(time.time()),
		"model": model,
		"choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if usage is None else [],
	}
	if usage is not None:
		body["usage"] = usage
	return body


class Handler(BaseHTTPRequestHandler):
	protocol_version = "HTTP/1.1"

	def log_message(self, *args):  # keep test output quiet
		pass

	def _send_json(self, status: int, payload: dict) -> None:
		data = json.dumps(payload).encode()
		self.send_response(status)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(data)))
		self.end_headers()
		self.wfile.write(data)

	def do_GET(self):
		if self.path.startswith("/_requests"):
			return self._send_json(200, {"requests": REQUESTS})
		if self.path.startswith("/v1/models"):
			return self._send_json(200, {"object": "list", "data": [{"id": "stub-model", "object": "model"}]})
		self._send_json(200, {"status": "ok"})

	def do_DELETE(self):
		REQUESTS.clear()
		self._send_json(200, {"cleared": True})

	def do_POST(self):
		length = int(self.headers.get("Content-Length") or 0)
		raw = self.rfile.read(length)
		content_type = self.headers.get("Content-Type", "")
		if content_type.startswith("multipart/form-data"):
			return self._transcription(content_type, raw)
		body = json.loads(raw or b"{}")
		self._record({"path": self.path, "body": body})
		if self.path.endswith("/embeddings"):
			return self._embeddings(body)
		if self.path.endswith("/chat/completions"):
			return self._chat(body)
		self._send_json(404, {"error": {"message": "not found"}})

	def _record(self, entry: dict) -> None:
		with LOG_LOCK:
			REQUESTS.append(entry)
			if LOG_PATH:
				with open(LOG_PATH, "a", encoding="utf-8") as handle:
					handle.write(json.dumps(entry) + "\n")

	def _transcription(self, content_type: str, raw: bytes) -> None:
		fields = parse_multipart(content_type, raw)
		filename, audio = fields.get("file", ("", b""))
		self._record(
			{
				"path": self.path,
				"body": {"model": fields.get("model"), "filename": filename, "bytes": len(audio)},
			}
		)
		if b"FAIL" in audio:
			return self._send_json(
				500, {"error": {"message": "stub transcription failure", "type": "server_error"}}
			)
		self._send_json(200, transcribe(audio))

	def _embeddings(self, body: dict) -> None:
		inputs = body.get("input", [])
		inputs = [inputs] if isinstance(inputs, str) else inputs
		data = [
			{"object": "embedding", "index": i, "embedding": embed(text)} for i, text in enumerate(inputs)
		]
		self._send_json(
			200,
			{
				"object": "list",
				"data": data,
				"model": body.get("model", "stub"),
				"usage": {"prompt_tokens": 1, "total_tokens": 1},
			},
		)

	def _chat(self, body: dict) -> None:
		model = body.get("model", "stub-model")
		decision = decide(body.get("messages", []))
		if decision.get("fail"):
			return self._send_json(
				500, {"error": {"message": "stub provider failure", "type": "server_error"}}
			)
		usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
		tool_call = (
			{
				"id": "call_" + hashlib.md5(json.dumps(decision, sort_keys=True).encode()).hexdigest()[:8],
				"type": "function",
				"function": {"name": decision["tool"], "arguments": json.dumps(decision["arguments"])},
			}
			if "tool" in decision
			else None
		)
		if not body.get("stream"):
			message = {"role": "assistant", "content": None if tool_call else decision["text"]}
			if tool_call:
				message["tool_calls"] = [tool_call]
			return self._send_json(
				200,
				{
					"id": "chatcmpl-stub",
					"object": "chat.completion",
					"created": int(time.time()),
					"model": model,
					"choices": [
						{
							"index": 0,
							"message": message,
							"finish_reason": "tool_calls" if tool_call else "stop",
						}
					],
					"usage": usage,
				},
			)

		self.send_response(200)
		self.send_header("Content-Type", "text/event-stream")
		self.send_header("Cache-Control", "no-cache")
		self.send_header("Transfer-Encoding", "chunked")
		self.end_headers()

		def emit(payload) -> None:
			raw = (
				"data: " + (payload if isinstance(payload, str) else json.dumps(payload)) + "\n\n"
			).encode()
			self.wfile.write(f"{len(raw):x}\r\n".encode() + raw + b"\r\n")
			self.wfile.flush()

		emit(_chunk(model, {"role": "assistant", "content": ""}))
		if tool_call:
			emit(
				_chunk(
					model,
					{"tool_calls": [{"index": 0, **{**tool_call, "function": {**tool_call["function"]}}}]},
				)
			)
			emit(_chunk(model, {}, finish="tool_calls"))
		else:
			words = decision["text"].split(" ")
			for position, word in enumerate(words):
				emit(_chunk(model, {"content": word + (" " if position < len(words) - 1 else "")}))
				if CHUNK_DELAY:
					time.sleep(CHUNK_DELAY)
			emit(_chunk(model, {}, finish="stop"))
		emit(_chunk(model, {}, usage=usage))
		emit("[DONE]")
		self.wfile.write(b"0\r\n\r\n")
		self.wfile.flush()


def main() -> None:
	global LOG_PATH, CHUNK_DELAY
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument("--port", type=int, default=11434)
	parser.add_argument("--host", default="127.0.0.1")
	parser.add_argument(
		"--delay", type=float, default=0.0, help="seconds to wait after each streamed text chunk"
	)
	parser.add_argument("--log", default=None, help="append each request body to this JSON-lines file")
	args = parser.parse_args()
	LOG_PATH = args.log
	CHUNK_DELAY = args.delay
	server = ThreadingHTTPServer((args.host, args.port), Handler)
	print(f"openai stub listening on http://{args.host}:{args.port}", flush=True)
	server.serve_forever()


if __name__ == "__main__":
	main()
