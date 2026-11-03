/** Record the microphone while the user holds the floor, and return the phrase as a WAV file. */

import { downsample, encodeWav } from "./wav";

/** 16 kHz is what speech recognition expects and keeps a phrase small (about 32 KB per second). */
export const TARGET_SAMPLE_RATE = 16000;
/** Recordings shorter than this are taps, not speech. */
export const MIN_RECORDING_MS = 300;
export const MAX_RECORDING_SECONDS = 60;
const PROCESSOR_BUFFER_SAMPLES = 4096;

export class VoiceRecorder {
	private stream: MediaStream | null = null;
	private context: AudioContext | null = null;
	private processor: ScriptProcessorNode | null = null;
	private chunks: Float32Array[] = [];
	private startedAt = 0;

	static supported(): boolean {
		return typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof AudioContext !== "undefined";
	}

	/** Ask for the microphone and start collecting audio. Rejects if the user refuses. */
	async start(): Promise<void> {
		this.stream = await navigator.mediaDevices.getUserMedia({
			audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
		});
		this.context = new AudioContext();
		const source = this.context.createMediaStreamSource(this.stream);
		this.processor = this.context.createScriptProcessor(PROCESSOR_BUFFER_SAMPLES, 1, 1);
		this.processor.onaudioprocess = (event) => this.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
		source.connect(this.processor);
		this.processor.connect(this.context.destination); // required for the processor to run; its output is silent
		this.chunks = [];
		this.startedAt = Date.now();
	}

	/** Stop recording and return the phrase as WAV, or null if it was too short to be speech. */
	async stop(): Promise<Blob | null> {
		const durationMs = Date.now() - this.startedAt;
		const sampleRate = this.context?.sampleRate ?? TARGET_SAMPLE_RATE;
		this.release();
		if (durationMs < MIN_RECORDING_MS) return null;
		const samples = downsample(concatenate(this.chunks), sampleRate, TARGET_SAMPLE_RATE);
		this.chunks = [];
		const wav = encodeWav(samples, Math.min(sampleRate, TARGET_SAMPLE_RATE));
		return new Blob([wav as BlobPart], { type: "audio/wav" });
	}

	/** Stop and throw the recording away. */
	cancel(): void {
		this.release();
		this.chunks = [];
	}

	private release(): void {
		this.processor?.disconnect();
		this.stream?.getTracks().forEach((track) => track.stop());
		void this.context?.close();
		this.processor = null;
		this.stream = null;
		this.context = null;
	}
}

function concatenate(chunks: Float32Array[]): Float32Array {
	const out = new Float32Array(chunks.reduce((total, chunk) => total + chunk.length, 0));
	let offset = 0;
	for (const chunk of chunks) {
		out.set(chunk, offset);
		offset += chunk.length;
	}
	return out;
}
