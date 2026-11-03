#!/usr/bin/env python3
# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""End-to-end scenarios against a running bench: web, worker, the FastAPI service and the stubs.

    python run_scenarios.py <site> <service-port> [--only NAME ...]

It talks to the real HTTP API (token auth), opens the real SSE stream, and reads back state by
running `e2e_checks` functions inside the site. Exit status is non-zero if any scenario fails.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import requests

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


# --- scenarios ---------------------------------------------------------------------------


def scenario_knowledge_and_search(ctx):
	"""Ingest a text source through the worker, then a chat run that searches it."""
	site = ctx["site"]
	source = in_site(site, "add_text_source", kb=ctx["kb"])
	state = wait_for(lambda: (s := in_site(site, "source_state", name=source))["status"] in ("Completed", "Failed") and s)
	check("ingest completes via worker", state and state["status"] == "Completed", json.dumps(state))
	check("chunks created", state and state["chunk_count"] > 0, f"chunks={state and state['chunk_count']}")
	check("index matches database", in_site(site, "index_consistency")["equal"], json.dumps(in_site(site, "index_consistency")))

	client = ctx["client"]
	init = client.call("start_run", input="What is the refund window? [[search: refund window days]]", agent=ctx["agent"])
	events = client.stream(init)
	kinds = types(events)
	check("stream has the expected event order", kinds[0] == "run_started" and kinds[-1] == "done", " ".join(kinds))
	done = events[-1]
	check("search tool returned the policy passage", "30 days" in str(done.get("output", "")), str(done.get("output"))[:120])
	check("run completed with usage", done.get("status") == "Completed" and done.get("usage"), json.dumps(done)[:160])
	state = in_site(site, "run_state", run=init["run"])
	check("run persisted as Completed", state["status"] == "Completed", json.dumps(state)[:200])
	detail = client.get("session_detail", session=init["session"])
	roles = [m["role"] for m in detail["execution"]["transcript"]] if detail.get("execution") else []
	check("transcript saved", len(roles) >= 2, f"roles={roles}")


def scenario_approval_flow(ctx):
	"""A confirmation-gated tool pauses, is approved once, and cannot be replayed."""
	site, client = ctx["site"], ctx["client"]
	args = {"content": "prefers email replies", "scope": "agent"}
	init = client.call("start_run", input=f"remember this [[tool: update_memory {json.dumps(args)}]]", agent=ctx["agent"])
	events = client.stream(init)
	done = events[-1]
	check("run pauses for approval", done.get("status") == "Paused" and done.get("questions"), json.dumps(done)[:200])
	check("memory not written before approval", not [m for m in in_site(site, "memories", agent=ctx["agent"]) if "email" in (m["content"] or "")])
	question = (done.get("questions") or [{}])[0]
	resumed = client.call("resume_run", run=init["run"], answers={question.get("key"): "Approve"})
	events = client.stream(resumed, answers={question.get("key"): "Approve"})
	check("approved run completes", events and events[-1].get("status") == "Completed", " ".join(types(events)))
	mem = [m for m in in_site(site, "memories", agent=ctx["agent"]) if "email" in (m["content"] or "")]
	check("memory written exactly once", len(mem) == 1, json.dumps(mem))
	state = in_site(site, "run_state", run=init["run"])
	check("approval consumed (single use)", state["approvals"] == {}, json.dumps(state["approvals"]))
	reply = requests.post(
		f"{WEB}/api/method/frappe_ai.api.dispatch.dispatch_plugin_tool",
		json={"tool": "update_memory", "user": "Administrator", "arguments": args, "run": init["run"], "call_id": question.get("key")},
		headers={"X-Frappe-AI-Service-Secret": ctx["secret"], "X-Frappe-Site-Name": site},
		timeout=30,
	)
	check("replayed approval is refused", reply.status_code in (401, 403, 417) or "approval" in reply.text.lower() or "not active" in reply.text.lower(), f"{reply.status_code} {reply.text[:120]}")


def scenario_deny_and_stop(ctx):
	site, client = ctx["site"], ctx["client"]
	args = {"content": "should never be saved", "scope": "agent"}
	init = client.call("start_run", input=f"[[tool: update_memory {json.dumps(args)}]]", agent=ctx["agent"])
	events = client.stream(init)
	question = (events[-1].get("questions") or [{}])[0]
	resumed = client.call("resume_run", run=init["run"], answers={question.get("key"): "Deny"})
	events = client.stream(resumed, answers={question.get("key"): "Deny"})
	check("denied run ends", events and events[-1]["type"] == "done", " ".join(types(events)))
	check("denied memory not written", not [m for m in in_site(site, "memories", agent=ctx["agent"]) if "never be saved" in (m["content"] or "")])

	init = client.call("start_run", input="plain question about nothing", agent=ctx["agent"])
	stopped = client.call("stop_run", run=init["run"])
	check("stop_run on a running run returns a status", stopped.get("status") in ("Failed", "Completed"), json.dumps(stopped))
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
	check("run marked Failed with a message", state["status"] == "Failed" and state["error"], json.dumps(state)[:160])


SCENARIOS = {
	"knowledge": scenario_knowledge_and_search,
	"approval": scenario_approval_flow,
	"deny-stop": scenario_deny_and_stop,
	"provider-error": scenario_provider_error,
}


def main() -> int:
	site, port = sys.argv[1], sys.argv[2]
	only = sys.argv[sys.argv.index("--only") + 1 :] if "--only" in sys.argv else list(SCENARIOS)
	seeded = in_site(site, "seed")
	secret = json.load(open(f"/var/tmp/e2e/work/frappe-bench/sites/{site}/site_config.json"))["frappe_ai_service_secret"]
	ctx = {"site": site, "port": port, "secret": secret, **seeded, "client": Client(site, seeded["token"])}
	requests.delete(f"{STUB}/_requests", timeout=5)
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
