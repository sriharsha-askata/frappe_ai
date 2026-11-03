#!/usr/bin/env python3
# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""End-to-end scenarios against a running bench: web, worker, the FastAPI service and the stubs.

    python run_scenarios.py <site> <service-port> [--only NAME ...]

It talks to the real HTTP API (token auth), opens the real SSE stream, and reads back state by
running `e2e_checks` functions inside the site. Exit status is non-zero if any scenario fails.

Scenarios (`--only` takes these names): knowledge, approval, deny-stop, provider-error, backends,
memory, triggers, attachment, parallel-budget, sessions-search, audio.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import requests
from audio import make_wav

WEB = os.environ.get("E2E_WEB", "http://127.0.0.1:8000")
RUN_IN_SITE = os.environ.get("E2E_RUN_IN_SITE", "/var/tmp/e2e/r.sh")
STUB = os.environ.get("E2E_STUB", "http://127.0.0.1:11434")

RESULTS: list[tuple[str, bool, str]] = []


def in_site(site: str, target: str, **kwargs):
	"""Call `e2e_checks.<target>` inside the site and return its JSON result."""
	cmd = [RUN_IN_SITE, site, f"e2e_checks.{target}"] + ([json.dumps(kwargs)] if kwargs else [])
	out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
	for line in out.stdout.splitlines():
		if line.startswith("RESULT "):
			return json.loads(line[7:])
	raise RuntimeError(f"{target} failed:\n{out.stdout[-1500:]}\n{out.stderr[-1500:]}")


class Client:
	def __init__(self, site: str, token: str):
		self.site = site
		self.http = requests.Session()
		self.http.headers.update({"Authorization": f"token {token}", "X-Frappe-Site-Name": site})

	def call(self, method: str, **params) -> dict:
		reply = self.http.post(f"{WEB}/api/method/frappe_ai.api.frontend.{method}", json=params, timeout=60)
		if reply.status_code != 200:
			raise RuntimeError(f"{method} -> {reply.status_code}: {reply.text[:300]}")
		return reply.json()["message"]

	def get(self, method: str, **params) -> dict:
		reply = self.http.get(f"{WEB}/api/method/frappe_ai.api.frontend.{method}", params=params, timeout=60)
		if reply.status_code != 200:
			raise RuntimeError(f"{method} -> {reply.status_code}: {reply.text[:300]}")
		return reply.json()["message"]

	@staticmethod
	def stream(init: dict, answers: dict | None = None) -> list[dict]:
		"""Open the SSE stream with the bearer run token and return every event."""
		events = []
		with requests.post(
			init["stream_url"],
			json={"answers": answers} if answers else {},
			headers={"Authorization": f"Bearer {init['token']}"},
			stream=True,
			timeout=120,
		) as reply:
			if reply.status_code != 200:
				return [{"type": "http_error", "status": reply.status_code, "body": reply.text[:300]}]
			for raw in reply.iter_lines(decode_unicode=True):
				if raw and raw.startswith("data: "):
					events.append(json.loads(raw[6:]))
		return events


def types(events: list[dict]) -> list[str]:
	return [e["type"] for e in events]


def check(name: str, ok: bool, detail: str = "") -> None:
	RESULTS.append((name, bool(ok), detail))
	print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}".rstrip(), flush=True)


def wait_for(fn, timeout=90, interval=1.0):
	deadline = time.time() + timeout
	value = fn()
	while not value and time.time() < deadline:
		time.sleep(interval)
		value = fn()
	return value


def clear_stub_log() -> None:
	requests.delete(f"{STUB}/_requests", timeout=5)


def first_chat_messages() -> list[dict]:
	"""The messages of the first chat request the stub received since the log was cleared."""
	seen = requests.get(f"{STUB}/_requests", timeout=5).json()["requests"]
	chats = [entry for entry in seen if entry["path"].endswith("/chat/completions")]
	return chats[0]["body"]["messages"] if chats else []


def text_of_role(messages: list[dict], role: str) -> str:
	return " ".join(message["content"] for message in messages if message["role"] == role)


def wait_for_ingestion(site: str, source: str) -> dict | None:
	"""Wait until the worker has finished (or failed) a knowledge source; returns its state."""

	def finished():
		state = in_site(site, "source_state", name=source)
		return state if state["status"] in ("Completed", "Failed") else None

	return wait_for(finished)


# --- scenarios ---------------------------------------------------------------------------


def scenario_knowledge_and_search(ctx):
	"""Ingest a text source through the worker, then a chat run that searches it."""
	site = ctx["site"]
	source = in_site(site, "add_text_source", kb=ctx["kb"])
	state = wait_for_ingestion(site, source)
	check("ingest completes via worker", state and state["status"] == "Completed", json.dumps(state))
	check("chunks created", state and state["chunk_count"] > 0, f"chunks={state and state['chunk_count']}")
	check(
		"index matches database",
		in_site(site, "index_consistency")["equal"],
		json.dumps(in_site(site, "index_consistency")),
	)

	client = ctx["client"]
	init = client.call(
		"start_run", input="What is the refund window? [[search: refund window days]]", agent=ctx["agent"]
	)
	events = client.stream(init)
	kinds = types(events)
	check(
		"stream has the expected event order",
		kinds[0] == "run_started" and kinds[-1] == "done",
		" ".join(kinds),
	)
	done = events[-1]
	check(
		"search tool returned the policy passage",
		"30 days" in str(done.get("output", "")),
		str(done.get("output"))[:120],
	)
	check(
		"run completed with usage",
		done.get("status") == "Completed" and done.get("usage"),
		json.dumps(done)[:160],
	)
	state = in_site(site, "run_state", run=init["run"])
	check("run persisted as Completed", state["status"] == "Completed", json.dumps(state)[:200])
	detail = client.get("session_detail", session=init["session"])
	roles = [m["role"] for m in detail["execution"]["transcript"]] if detail.get("execution") else []
	check("transcript saved", len(roles) >= 2, f"roles={roles}")


def scenario_approval_flow(ctx):
	"""A confirmation-gated tool pauses, is approved once, and cannot be replayed."""
	site, client = ctx["site"], ctx["client"]
	in_site(site, "clear_memories", agent=ctx["agent"])
	args = {"content": "prefers email replies", "scope": "agent"}
	init = client.call(
		"start_run", input=f"remember this [[tool: update_memory {json.dumps(args)}]]", agent=ctx["agent"]
	)
	events = client.stream(init)
	done = events[-1]
	check(
		"run pauses for approval",
		done.get("status") == "Paused" and done.get("questions"),
		json.dumps(done)[:200],
	)
	check(
		"memory not written before approval",
		not [m for m in in_site(site, "memories", agent=ctx["agent"]) if "email" in (m["content"] or "")],
	)
	question = (done.get("questions") or [{}])[0]
	resumed = client.call("resume_run", run=init["run"], answers={question.get("key"): "Approve"})
	events = client.stream(resumed, answers={question.get("key"): "Approve"})
	check(
		"approved run completes", events and events[-1].get("status") == "Completed", " ".join(types(events))
	)
	mem = [m for m in in_site(site, "memories", agent=ctx["agent"]) if "email" in (m["content"] or "")]
	check("memory written exactly once", len(mem) == 1, json.dumps(mem))
	state = in_site(site, "run_state", run=init["run"])
	check("approval consumed (single use)", state["approvals"] == {}, json.dumps(state["approvals"]))
	reply = requests.post(
		f"{WEB}/api/method/frappe_ai.api.dispatch.dispatch_plugin_tool",
		json={
			"tool": "update_memory",
			"user": "Administrator",
			"arguments": args,
			"run": init["run"],
			"call_id": question.get("key"),
		},
		headers={"X-Frappe-AI-Service-Secret": ctx["secret"], "X-Frappe-Site-Name": site},
		timeout=30,
	)
	check(
		"replayed approval is refused",
		reply.status_code in (401, 403, 417)
		or "approval" in reply.text.lower()
		or "not active" in reply.text.lower(),
		f"{reply.status_code} {reply.text[:120]}",
	)


def scenario_deny_and_stop(ctx):
	site, client = ctx["site"], ctx["client"]
	args = {"content": "should never be saved", "scope": "agent"}
	init = client.call("start_run", input=f"[[tool: update_memory {json.dumps(args)}]]", agent=ctx["agent"])
	events = client.stream(init)
	question = (events[-1].get("questions") or [{}])[0]
	resumed = client.call("resume_run", run=init["run"], answers={question.get("key"): "Deny"})
	events = client.stream(resumed, answers={question.get("key"): "Deny"})
	check("denied run ends", events and events[-1]["type"] == "done", " ".join(types(events)))
	check(
		"denied memory not written",
		not [
			m
			for m in in_site(site, "memories", agent=ctx["agent"])
			if "never be saved" in (m["content"] or "")
		],
	)

	init = client.call("start_run", input="plain question about nothing", agent=ctx["agent"])
	stopped = client.call("stop_run", run=init["run"])
	check(
		"stop_run on a running run returns a status",
		stopped.get("status") in ("Failed", "Completed"),
		json.dumps(stopped),
	)
	events = client.stream(init)
	state = in_site(site, "run_state", run=init["run"])
	check("a stopped run stays stopped", state["status"] in ("Failed", "Completed"), state["status"])
	again = client.call("stop_run", run=init["run"])
	check("stop_run is idempotent", again.get("status") == state["status"], json.dumps(again))


def scenario_provider_error(ctx):
	site, client = ctx["site"], ctx["client"]
	init = client.call("start_run", input="[[fail]]", agent=ctx["agent"])
	events = client.stream(init)
	check("provider failure surfaces as an error event", "error" in types(events), " ".join(types(events)))
	state = in_site(site, "run_state", run=init["run"])
	check(
		"run marked Failed with a message",
		state["status"] == "Failed" and state["error"],
		json.dumps(state)[:160],
	)


def scenario_backend_matrix(ctx):
	"""Switch the vector backend, rebuild, and search - for every backend this database supports."""
	site, client = ctx["site"], ctx["client"]
	db = in_site(site, "install_state")[0]["detail"]
	wanted = ["pgvector", "lancedb", "chroma"] if db == "postgres" else ["lancedb", "chroma"]
	in_site(site, "reset_knowledge", kb=ctx["kb"])
	source = in_site(site, "add_text_source", kb=ctx["kb"], title="Matrix")
	wait_for_ingestion(site, source)
	for name in wanted:
		in_site(site, "set_backend", name=name)
		before = in_site(site, "retrieve", query="refund window", kb=ctx["kb"])
		if name != wanted[0]:
			check(
				f"{name}: unbuilt index fails clearly (not a crash)",
				(not before["ok"]) or before["count"] == 0,
				json.dumps(before)[:140],
			)
		report = in_site(site, "rebuild_index")
		check(
			f"{name}: rebuild completes",
			report["status"] == "completed" and report["backend"] == name,
			json.dumps(report),
		)
		consistent = in_site(site, "index_consistency")
		check(f"{name}: index matches database after rebuild", consistent["equal"], json.dumps(consistent))
		found = in_site(site, "retrieve", query="refund window days", kb=ctx["kb"])
		check(
			f"{name}: retrieval finds the policy",
			found["ok"] and any("Refund" in c for c in found["contents"]),
			json.dumps(found)[:160],
		)
		init = client.call("start_run", input="refunds? [[search: refund window days]]", agent=ctx["agent"])
		events = client.stream(init)
		check(
			f"{name}: chat search works through the service",
			events[-1].get("status") == "Completed" and "30 days" in str(events[-1].get("output")),
			str(events[-1])[:120],
		)
	try:
		in_site(site, "set_backend", name="nonsense")
		check("unknown backend is rejected on save", False, "accepted")
	except RuntimeError as error:
		check("unknown backend is rejected on save", "Unknown vector backend" in str(error), "rejected")
	in_site(site, "set_backend", name="Auto")
	in_site(site, "rebuild_index")


def scenario_memory_prompt(ctx):
	"""More than 20 memories: the relevant one reaches the model's system prompt, not all of them."""
	site, client = ctx["site"], ctx["client"]
	in_site(site, "clear_memories", agent=ctx["agent"])
	special = in_site(site, "add_memories", agent=ctx["agent"], count=30, special_word="zanzibar")["special"]
	clear_stub_log()
	init = client.call("start_run", input="who manages the zanzibar account?", agent=ctx["agent"])
	client.stream(init)
	messages = first_chat_messages()
	system = text_of_role(messages, "system")
	check("memory block is in the system prompt", "<agent_memory>" in system)
	check("the relevant memory was selected", special in system)
	listed = system.count("filler fact number")
	check("not every memory is injected", 0 < listed < 30, f"{listed} of 30 filler shown")
	in_site(site, "clear_memories", agent=ctx["agent"])


def scenario_triggers(ctx):
	site = ctx["site"]
	event = in_site(site, "make_trigger", agent=ctx["agent"], kind="event")["name"]
	in_site(site, "insert_todo", description="trigger me")
	runs = wait_for(
		lambda: [
			r
			for r in in_site(site, "trigger_runs", trigger=event)
			if r["status"] in ("Completed", "Failed", "Paused")
		]
	)
	check(
		"doctype-event trigger starts a run through the worker",
		runs and runs[0]["status"] == "Completed",
		json.dumps(runs)[:200],
	)
	scheduled = in_site(site, "make_trigger", agent=ctx["agent"], kind="scheduled")["name"]
	start = time.time() + 4
	procs = [
		subprocess.Popen(
			[
				RUN_IN_SITE,
				site,
				"e2e_checks.claim_window",
				json.dumps({"trigger": scheduled, "start_at": start}),
			],
			stdout=subprocess.PIPE,
			text=True,
		)
		for _ in range(2)
	]
	outcomes = []
	for proc in procs:
		out = proc.communicate(timeout=120)[0]
		outcomes.append(
			next((json.loads(line[7:]) for line in out.splitlines() if line.startswith("RESULT ")), None)
		)
	won = [o["won"] for o in outcomes if o]
	check(
		"two processes race for one scheduled window: exactly one wins",
		sorted(won) == [False, True],
		json.dumps(outcomes),
	)
	scheduled2 = in_site(site, "make_trigger", agent=ctx["agent"], kind="scheduled")["name"]
	known = {
		r["name"] for r in in_site(site, "trigger_runs", trigger=scheduled2)
	}  # runs left by earlier executions
	in_site(site, "fire_due_scheduled")
	wait_for(
		lambda: [
			r
			for r in in_site(site, "trigger_runs", trigger=scheduled2)
			if r["name"] not in known and r["status"] in ("Completed", "Failed", "Paused")
		]
	)
	new = [r for r in in_site(site, "trigger_runs", trigger=scheduled2) if r["name"] not in known]
	check(
		"scheduled trigger fires once when due",
		len(new) == 1 and new[0]["status"] == "Completed",
		json.dumps(new)[:200],
	)
	in_site(site, "fire_due_scheduled")
	time.sleep(3)
	again = [r for r in in_site(site, "trigger_runs", trigger=scheduled2) if r["name"] not in known]
	check("the same window does not fire twice", len(again) == 1, f"runs={len(again)}")


def scenario_attachment_retrieval(ctx):
	"""A file too big to inline is chunked, retrieved for the question, and cleaned up with the session."""
	site, client = ctx["site"], ctx["client"]
	filler = " ".join(f"paragraph {i} discusses unrelated warehouse scheduling topics." for i in range(2600))
	body = filler + " The secret launch codename is Bluebird-77. " + filler
	files = {"file": ("big-notes.txt", body.encode(), "text/plain")}
	reply = client.http.post(
		f"{WEB}/api/method/frappe_ai.api.frontend.upload_attachment", files=files, timeout=60
	)
	check("upload returns a staged attachment", reply.status_code == 200, reply.text[:120])
	staged = reply.json()["message"]["attachment"]
	clear_stub_log()
	init = client.call(
		"start_run", input="what is the launch codename?", agent=ctx["agent"], attachments=[staged["file"]]
	)
	events = client.stream(init)
	check(
		"run with a large attachment completes",
		events[-1].get("status") == "Completed",
		str(events[-1])[:120],
	)
	messages = first_chat_messages()
	user_text = text_of_role(messages, "user")
	check(
		"the relevant chunk was injected (retrieval, not the whole file)",
		"Bluebird-77" in user_text and len(user_text) < len(body) / 2,
		f"user chars={len(user_text)} file chars={len(body)}",
	)
	state = in_site(site, "attachment_index_count")
	check("attachment chunks are indexed", state["exists"] and state["count"] > 0, json.dumps(state))
	in_site(site, "delete_session", session=init["session"])
	state = in_site(site, "attachment_index_count")
	check(
		"deleting the session removes its chunks",
		(not state["exists"]) or state["count"] == 0,
		json.dumps(state),
	)
	noisy = [m for m in in_site(site, "error_log_tail") if m and "cleanup" in m.lower()]
	check("no swallowed cleanup errors", not noisy, json.dumps(noisy))


def scenario_parallel_budget(ctx):
	"""Parallel tool calls on one run must each be counted (row lock), on every database."""
	import concurrent.futures

	site, client = ctx["site"], ctx["client"]
	init = client.call("start_run", input="parallel", agent=ctx["agent"])

	def call(i):
		reply = requests.post(
			f"{WEB}/api/method/frappe_ai.api.dispatch.dispatch_plugin_tool",
			json={
				"tool": "search_knowledge",
				"user": "Administrator",
				"arguments": {"query": "refund"},
				"run": init["run"],
				"call_id": f"c{i}",
			},
			headers={"X-Frappe-AI-Service-Secret": ctx["secret"], "X-Frappe-Site-Name": site},
			timeout=120,
		)
		return reply.status_code

	with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
		codes = list(pool.map(call, range(8)))
	state = in_site(site, "run_state", run=init["run"])
	counted = (state["budget"] or {}).get("tool_calls")
	check("all parallel dispatches succeeded", all(c == 200 for c in codes), str(codes))
	check("every parallel call was counted exactly once", counted == 8, f"tool_calls={counted}")


def scenario_sessions_search(ctx):
	client = ctx["client"]
	in_site(ctx["site"], "session_titles", prefix="E2E")
	rows = client.get("sessions", query="100%_")["session"]["history"]
	titles = [r["title"] for r in rows]
	check(
		"% and _ are matched literally in session search",
		any("100%_match" in t for t in titles) and not any("plain other" in t for t in titles),
		json.dumps(titles)[:200],
	)


def stub_transcription_requests() -> int:
	"""How many speech-to-text requests the stub has received."""
	requests_seen = requests.get(f"{STUB}/_requests", timeout=5).json()["requests"]
	return sum(1 for entry in requests_seen if entry["path"].endswith("/audio/transcriptions"))


def upload_audio(client: Client, wav: bytes):
	return client.http.post(
		f"{WEB}/api/method/frappe_ai.api.frontend.upload_attachment",
		files={"file": ("voice-note.wav", wav, "audio/wav")},
		timeout=120,
	)


def scenario_audio(ctx):
	"""Audio is refused until a transcription model is set, then transcribed into the chat and the knowledge base."""
	site, client = ctx["site"], ctx["client"]
	spoken = "The quarterly review moved to Thursday. Bring the vendor contracts."
	wav = make_wav(spoken)

	in_site(site, "set_transcription", enabled=False)
	reply = upload_audio(client, wav)
	check(
		"audio is refused while no model is configured",
		reply.status_code != 200 and "transcription model" in reply.text,
		reply.text[:140],
	)
	check("nothing was sent to the provider", stub_transcription_requests() == 0)

	in_site(site, "set_transcription", enabled=True)
	reply = upload_audio(client, wav)
	check("audio is accepted once a model is configured", reply.status_code == 200, reply.text[:140])
	staged = reply.json()["message"]["attachment"]
	check(
		"the composer is told audio is supported",
		"wav" in client.get("bootstrap")["composer"]["supported_file_types"],
	)

	clear_stub_log()
	init = client.call(
		"start_run", input="what was decided?", agent=ctx["agent"], attachments=[staged["file"]]
	)
	events = client.stream(init)
	check(
		"a chat run with the audio attachment completes",
		events[-1].get("status") == "Completed",
		str(events[-1])[:120],
	)
	messages = first_chat_messages()
	user_text = text_of_role(messages, "user")
	check(
		"the transcript reached the model",
		"Thursday" in user_text and "[00:00:00]" in user_text,
		user_text[:160],
	)
	check(
		"the second sentence is timestamped five seconds later",
		"[00:00:05] Bring the vendor contracts." in user_text,
	)

	in_site(site, "set_transcription", enabled=True, max_audio_mb=1)
	before = stub_transcription_requests()
	reply = upload_audio(client, make_wav(spoken, padding_bytes=2 * 1024 * 1024))
	check(
		"an oversized file is refused with the limit",
		reply.status_code != 200 and "limit is 1 MB" in reply.text,
		reply.text[:140],
	)
	check("an oversized file is never sent to the provider", stub_transcription_requests() == before)

	in_site(site, "set_transcription", enabled=True)
	reply = upload_audio(client, make_wav("FAIL"))
	check(
		"a provider failure is reported to the user",
		reply.status_code != 200 and "Provider" in reply.text,
		reply.text[:160],
	)

	source = in_site(
		site, "add_audio_source", kb=ctx["kb"], spoken="The hardware warranty lasts exactly seven years."
	)
	state = wait_for_ingestion(site, source)
	check(
		"an audio knowledge source is ingested through the worker",
		state and state["status"] == "Completed",
		json.dumps(state),
	)
	found = in_site(site, "retrieve", query="warranty seven years", kb=ctx["kb"])
	check(
		"search finds a sentence from the recording",
		found["ok"] and any("seven years" in c for c in found["contents"]),
		json.dumps(found)[:160],
	)
	check("index matches database", in_site(site, "index_consistency")["equal"])
	in_site(site, "set_transcription", enabled=False)


SCENARIOS = {
	"knowledge": scenario_knowledge_and_search,
	"approval": scenario_approval_flow,
	"deny-stop": scenario_deny_and_stop,
	"provider-error": scenario_provider_error,
	"backends": scenario_backend_matrix,
	"memory": scenario_memory_prompt,
	"triggers": scenario_triggers,
	"attachment": scenario_attachment_retrieval,
	"parallel-budget": scenario_parallel_budget,
	"sessions-search": scenario_sessions_search,
	"audio": scenario_audio,
}


def main() -> int:
	site, port = sys.argv[1], sys.argv[2]
	only = sys.argv[sys.argv.index("--only") + 1 :] if "--only" in sys.argv else list(SCENARIOS)
	seeded = in_site(site, "seed")
	secret = json.load(open(f"/var/tmp/e2e/work/frappe-bench/sites/{site}/site_config.json"))[
		"frappe_ai_service_secret"
	]
	ctx = {"site": site, "port": port, "secret": secret, **seeded, "client": Client(site, seeded["token"])}
	clear_stub_log()
	for name in only:
		print(f"\n== {site}: {name}", flush=True)
		try:
			SCENARIOS[name](ctx)
		except Exception as error:  # a crashed scenario is a failure, not a skipped test
			check(f"{name} ran without an exception", False, repr(error)[:300])
	failed = [r for r in RESULTS if not r[1]]
	print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed on {site}")
	return 1 if failed else 0


if __name__ == "__main__":
	sys.exit(main())
