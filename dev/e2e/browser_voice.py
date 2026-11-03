#!/usr/bin/env python3
# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Drive voice mode in a real Chromium: click the microphone, speak, hear the reply requested.

    python browser_voice.py <site> [--keep-screenshot PATH]

Chromium is started with a fake microphone that plays a WAV file, so no hardware is needed. The stub
(`openai_stub.py`, started with `--transcript`) turns that audio into a fixed request, and the script
checks, from the stub's request log, that the phrase was transcribed, the chat run used it, and the
reply was sent to the speech endpoint. It prints one PASS/FAIL line per check.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
import time
import wave

import requests
from playwright.sync_api import sync_playwright

WEB = os.environ.get("E2E_WEB", "http://127.0.0.1:8000")
STUB = os.environ.get("E2E_STUB", "http://127.0.0.1:11434")
CHROMIUM = os.environ.get("E2E_CHROMIUM", "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome")
SPOKEN_SECONDS = 2
SAMPLE_RATE_HZ = 16000
FAKE_PHRASE = "Tell me about refunds"

RESULTS: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
	RESULTS.append(bool(ok))
	print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}".rstrip(), flush=True)


def write_tone(path: str) -> None:
	"""A few seconds of loud tone, which the fake microphone repeats."""
	with wave.open(path, "wb") as wav:
		wav.setnchannels(1)
		wav.setsampwidth(2)
		wav.setframerate(SAMPLE_RATE_HZ)
		frames = (
			int(9000 * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE_HZ))
			for i in range(SPOKEN_SECONDS * SAMPLE_RATE_HZ)
		)
		wav.writeframes(b"".join(struct.pack("<h", sample) for sample in frames))


def stub_requests(suffix: str) -> list[dict]:
	seen = requests.get(f"{STUB}/_requests", timeout=5).json()["requests"]
	return [entry for entry in seen if entry["path"].endswith(suffix)]


def log_in(context) -> None:
	"""Sign in through the API; the session cookie then belongs to the browser context."""
	reply = context.request.post(f"{WEB}/api/method/login", data={"usr": "Administrator", "pwd": "admin"})
	if not reply.ok:
		raise RuntimeError(f"login failed: {reply.status} {reply.text()[:200]}")


def run(site: str, screenshot: str | None) -> None:
	tone = os.path.join(tempfile.mkdtemp(prefix="voice-"), "tone.wav")
	write_tone(tone)
	requests.delete(f"{STUB}/_requests", timeout=5)
	with sync_playwright() as playwright:
		browser = playwright.chromium.launch(
			executable_path=CHROMIUM,
			args=[
				"--use-fake-ui-for-media-stream",
				"--use-fake-device-for-media-stream",
				f"--use-file-for-fake-audio-capture={tone}",
				"--autoplay-policy=no-user-gesture-required",
				"--no-sandbox",
			],
		)
		context = browser.new_context(
			viewport={"width": 1440, "height": 1000},
			extra_http_headers={"X-Frappe-Site-Name": site}, permissions=["microphone"]
		)
		page = context.new_page()
		errors: list[str] = []
		page.on("pageerror", lambda error: errors.append(str(error)))
		log_in(context)
		page.goto(f"{WEB}/desk/frappe-ai", wait_until="domcontentloaded")
		mic = page.locator(".faip-shell button.faip-voice-button")
		mic.wait_for(state="attached", timeout=30000)
		page.keyboard.press("Control+i")  # open the slide-in panel
		page.wait_for_timeout(600)
		check("the microphone button is shown when voice is available", mic.count() == 1)

		mic.click()
		page.wait_for_selector(".faip-shell button.faip-voice-button.is-recording", timeout=10000)
		check("clicking it starts listening", True)
		time.sleep(2.5)  # let the fake microphone play a couple of seconds
		mic.click()

		page.wait_for_selector(f"text={FAKE_PHRASE}", timeout=30000)
		check("the transcribed phrase appears as the user's message", True)
		transcriptions = stub_requests("/audio/transcriptions")
		check(
			"one phrase was sent for transcription",
			len(transcriptions) == 1,
			f"{len(transcriptions)} request(s)",
		)
		check(
			"the recording was a WAV of the spoken length",
			40000 < transcriptions[0]["body"]["bytes"] < 200000 if transcriptions else False,
			str(transcriptions[0]["body"]) if transcriptions else "",
		)

		deadline = time.time() + 30
		while time.time() < deadline and not stub_requests("/audio/speech"):
			time.sleep(0.3)
		spoken = stub_requests("/audio/speech")
		check("the reply was sent to the speech endpoint", bool(spoken), json_text(spoken))
		check(
			"what was spoken is the model's answer",
			bool(spoken) and FAKE_PHRASE.lower() in spoken[0]["body"]["input"].lower(),
			json_text(spoken),
		)
		chats = stub_requests("/chat/completions")
		check(
			"the chat run used the transcribed phrase",
			any(FAKE_PHRASE in str(c["body"]["messages"]) for c in chats),
		)
		check("the page raised no script errors", not errors, "; ".join(errors)[:200])
		if screenshot:
			page.screenshot(path=screenshot)
		browser.close()


def json_text(items: list[dict]) -> str:
	return str([i["body"] for i in items])[:160]


def main() -> int:
	site = sys.argv[1]
	screenshot = (
		sys.argv[sys.argv.index("--keep-screenshot") + 1] if "--keep-screenshot" in sys.argv else None
	)
	run(site, screenshot)
	print(f"\n{sum(RESULTS)}/{len(RESULTS)} checks passed")
	return 0 if all(RESULTS) else 1


if __name__ == "__main__":
	sys.exit(main())
