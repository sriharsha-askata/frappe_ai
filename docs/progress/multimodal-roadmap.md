# Multimodal roadmap

Where image and video support stands, and how far the application can be extended. Based on the current code:
`AI Session Attachment` stores `file`, `mode` and `extracted_text`; `AI Model` has no capability flags; images go
through OCR only (`knowledge/extract.py`); `_to_agno_messages` builds text-only messages; there is no video path.

## Principles
- Each horizon is its own branch with an ADR, a plan first, and e2e tests against the stub server.
- Capabilities are explicit flags on the model. Nothing is assumed to be supported.
- Media follows the same permission, budget and audit rules as text. Original media is kept only as long as needed.
- Every provider call goes through the existing OpenAI-compatible client, so providers stay swappable.

## Horizon 1: the model sees images (about 1 week)
- **Capability flags on `AI Model`:** `supports_vision`, `max_image_mb`, `max_images_per_turn` (sensible defaults).
- **Attachment modes:** add a `vision` mode next to extract and knowledge.
  - Images are downscaled (longest side up to about 1568 px), re-encoded, and attached to the user message as image parts.
  - A model without vision gives a clear message with options: use OCR text only, or switch model.
- **Service change:** `_to_agno_messages` and the builder pass images to Agno. The transcript stores a file reference,
  not base64, so history stays small and replays re-read the file.
- **Cost and limits:** count image tokens against the model's context window (this also fixes the open item where
  attachment routing ignores the context window). Respect budgets. Tools never receive image bytes.
- **Frontend:** thumbnails in the composer, paste and drop, a lightbox in the transcript.
- **Tests:** the stub chat server asserts the image part arrives; a non-vision model refuses cleanly; oversize and
  corrupt files; both databases.

## Horizon 2: media in knowledge and search (1-2 weeks)
- **Captions at indexing:** a vision model captions images; caption plus OCR text is indexed, with provenance
  (`source=caption|ocr`) so answers can cite it. OCR first; caption when OCR yields little text.
- **Documents with figures:** PDF and DOCX figures get the same treatment, capped per document.
- **Storage:** the `File` doctype stays the source of truth. Add an optional object-store adapter (S3/MinIO) behind a
  small interface like the vector backends. Local disk stays the default.
- **Retention:** per-doctype TTL for raw media; extracted text and captions are kept.
- **Re-index:** a re-caption action when the vision model changes, reusing `rebuild_knowledge_index`.

## Horizon 3: video (2-3 weeks)
- **Pipeline:** ffmpeg extracts the audio, transcribed with the existing audio code (timestamps on). Keyframes at scene
  changes or intervals are captioned by the vision model. Both merge into one timeline: `[mm:ss] says ... / shows ...`.
- **Guardrails:** maximum duration and size, a frame budget, background job with progress and cancel (extraction is
  synchronous today, so this is new). Rejected with an install hint when ffmpeg is missing.
- **Output:** indexed like a transcript, so "where did they mention refunds" returns a timestamp.
- **Not included:** native video-understanding models; add later as a provider adapter.

## Horizon 4: multimodal output and richer tools (2-4 weeks)
- Render images returned by tools (charts, previews) inline; generated images are saved as attachments on the run.
- Optional image-generation tool (`/images/generations`) with permission and budget checks, off by default per agent.
- Typed tools: describe an attached screenshot; extract a table from an image into structured rows.
- Vision on Frappe data: file fields on documents (invoices, receipts) passed to the model under the record's permissions.

## Horizon 5: computer-vision tools, opt-in (3-6 weeks)
- OpenCV, barcode/QR, detection run in the FastAPI service as an optional extra (`[vision]`), never in Frappe web workers.
- Tools: barcode/QR scan, image quality check, deskew and crop for OCR, object counts, before/after comparison.
- Heavier detectors or segmenters are configured as model endpoints, not bundled.
- Face recognition and identity matching stay out unless explicitly needed, with consent controls.

## Horizon 6: realtime and hardware (later, as needed)
- Camera capture in the browser (photo or a few frames) feeding Horizon 1.
- Hands-free voice (voice-mode Phase 4) and realtime speech-to-speech, with approvals kept click-only.
- Native non-OpenAI provider formats once a second provider matters.

## Cross-cutting
- **Models page:** capability matrix (text, vision, audio in, speech out) with a test button per capability.
- **Privacy:** state per setting that media goes to the configured provider; agent-level switch to block media
  sending; strip EXIF location.
- **Observability:** per-run counts of images, frames and audio seconds; cost per modality in usage.
- **Docs:** a "what you can attach" table generated from `supported_extensions()` and the model flags.

## Risks
- **Provider differences** in image limits and formats: capability flags and downscaling.
- **Cost and latency** of vision tokens: caps, caption-once indexing, budgets.
- **Prompt injection through images:** text read from images is untrusted like typed text; tool calls still need approvals.
- **Background jobs** for video: use Frappe's job queue with timeouts and status, not web workers.

## Suggested order
Horizon 1, then 2, then 3. Choose 4 and 5 by actual need. Horizon 1 alone covers screenshots, receipts and diagrams.
