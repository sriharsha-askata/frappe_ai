import assert from "node:assert/strict";
import test from "node:test";
import { downsample, encodeWav, floatToInt16 } from "../src/lib/wav";

function text(bytes: Uint8Array, start: number, length: number): string {
	return String.fromCharCode(...bytes.slice(start, start + length));
}

test("the WAV header describes mono 16-bit PCM at the given rate", () => {
	const wav = encodeWav(new Float32Array(160), 16000);
	const view = new DataView(wav.buffer);
	assert.equal(text(wav, 0, 4), "RIFF");
	assert.equal(text(wav, 8, 4), "WAVE");
	assert.equal(text(wav, 12, 4), "fmt ");
	assert.equal(view.getUint16(20, true), 1, "PCM");
	assert.equal(view.getUint16(22, true), 1, "mono");
	assert.equal(view.getUint32(24, true), 16000, "sample rate");
	assert.equal(view.getUint32(28, true), 32000, "bytes per second");
	assert.equal(view.getUint16(34, true), 16, "bits per sample");
	assert.equal(text(wav, 36, 4), "data");
});

test("the declared sizes match the data that follows", () => {
	const wav = encodeWav(new Float32Array(100), 8000);
	const view = new DataView(wav.buffer);
	assert.equal(wav.length, 44 + 200);
	assert.equal(view.getUint32(40, true), 200, "data bytes");
	assert.equal(view.getUint32(4, true), wav.length - 8, "RIFF size");
});

test("samples are written little-endian after the header", () => {
	const wav = encodeWav(new Float32Array([0, 0.5, -0.5]), 8000);
	const view = new DataView(wav.buffer);
	assert.equal(view.getInt16(44, true), 0);
	assert.equal(view.getInt16(46, true), Math.trunc(0.5 * 0x7fff));
	assert.equal(view.getInt16(48, true), -0.5 * 0x8000);
});

test("loud samples are clipped instead of wrapping around", () => {
	const out = floatToInt16(new Float32Array([2, -2, 1, -1]));
	assert.deepEqual(Array.from(out), [0x7fff, -0x8000, 0x7fff, -0x8000]);
});

test("downsampling averages the samples that fall into each output sample", () => {
	const out = downsample(new Float32Array([1, 1, 0, 0, 1, 1, 0, 0]), 48000, 16000 * 2);
	assert.equal(out.length, 5);
	const third = downsample(new Float32Array([0.3, 0.6, 0.9, 0.3, 0.6, 0.9]), 48000, 16000);
	assert.equal(third.length, 2);
	assert.ok(Math.abs(third[0] - 0.6) < 1e-6);
});

test("a rate that is not higher than the target is left alone", () => {
	const input = new Float32Array([0.1, 0.2]);
	assert.equal(downsample(input, 16000, 16000), input);
	assert.equal(downsample(input, 8000, 16000), input);
});
