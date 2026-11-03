/** Encode microphone samples as a WAV file the transcription service accepts. */

const WAV_HEADER_BYTES = 44;
const BYTES_PER_SAMPLE = 2;
const PCM_FORMAT = 1;
const INT16_MAX = 0x7fff;
const INT16_MIN_MAGNITUDE = 0x8000;

/** Convert floating point samples in [-1, 1] to 16-bit integers, clipping anything outside. */
export function floatToInt16(samples: Float32Array): Int16Array {
	const out = new Int16Array(samples.length);
	for (let i = 0; i < samples.length; i++) {
		const clipped = Math.max(-1, Math.min(1, samples[i]));
		out[i] = clipped < 0 ? clipped * INT16_MIN_MAGNITUDE : clipped * INT16_MAX;
	}
	return out;
}

/** Reduce the sample rate by averaging the input samples that fall into each output sample. */
export function downsample(samples: Float32Array, inputRate: number, outputRate: number): Float32Array {
	if (outputRate >= inputRate) return samples;
	const ratio = inputRate / outputRate;
	const out = new Float32Array(Math.floor(samples.length / ratio));
	for (let i = 0; i < out.length; i++) {
		const start = Math.floor(i * ratio);
		const end = Math.min(samples.length, Math.floor((i + 1) * ratio));
		let sum = 0;
		for (let j = start; j < end; j++) sum += samples[j];
		out[i] = sum / Math.max(1, end - start);
	}
	return out;
}

/** A mono 16-bit PCM WAV file: a 44-byte header followed by the samples. */
export function encodeWav(samples: Float32Array, sampleRate: number): Uint8Array {
	const pcm = floatToInt16(samples);
	const dataBytes = pcm.length * BYTES_PER_SAMPLE;
	const buffer = new ArrayBuffer(WAV_HEADER_BYTES + dataBytes);
	const view = new DataView(buffer);
	writeHeader(view, sampleRate, dataBytes);
	pcm.forEach((value, i) => view.setInt16(WAV_HEADER_BYTES + i * BYTES_PER_SAMPLE, value, true));
	return new Uint8Array(buffer);
}

function writeHeader(view: DataView, sampleRate: number, dataBytes: number): void {
	writeText(view, 0, "RIFF");
	view.setUint32(4, WAV_HEADER_BYTES - 8 + dataBytes, true);
	writeText(view, 8, "WAVE");
	writeText(view, 12, "fmt ");
	view.setUint32(16, 16, true); // size of the format block
	view.setUint16(20, PCM_FORMAT, true);
	view.setUint16(22, 1, true); // mono
	view.setUint32(24, sampleRate, true);
	view.setUint32(28, sampleRate * BYTES_PER_SAMPLE, true); // bytes per second
	view.setUint16(32, BYTES_PER_SAMPLE, true); // bytes per frame
	view.setUint16(34, BYTES_PER_SAMPLE * 8, true); // bits per sample
	writeText(view, 36, "data");
	view.setUint32(40, dataBytes, true);
}

function writeText(view: DataView, offset: number, text: string): void {
	for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
}
