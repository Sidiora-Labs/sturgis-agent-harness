---
title: "Connect speech and image services"
description: "Configure transcription, speech synthesis, and image generation using the supported media-service contracts."
---

Sturgis can request transcription, spoken audio, or an image from compatible services. It does not bundle those services, their weights, a microphone recorder, or a universal vendor account. Each kind has a specific request and result format.

## Shared setup

In Connections, choose **Speech to text**, **Text to speech**, or **Image generation**. Supply the service's base endpoint, credential reference if needed, and **Model (optional)**. Text to speech also has **Voice (optional)**. Save, grant the appropriate operation, and test readiness.

Readiness requests `/models` under the base endpoint and checks the selected model. Although the form labels the field optional, the current media probe requires an explicit model that exists in that catalog; leaving it blank does not select one automatically. The evidence explicitly has `operation_verified:false`: catalog membership is not a successful transcription, speech, or image generation.

## Worked example: transcribe an uploaded recording

Upload a short audio source to the agent and obtain its `artifact_id` from attachment metadata; it is different from the attachment ID. Configure **Speech to text**, grant **Transcribe audio attachments**, and use an actual tool-capable model to request transcription of that artifact.

The tool is `connection.CONNECTION_ID.transcribe` and accepts `artifact_id`. The adapter sends multipart `model` and `file` to `/audio/transcriptions`; it expects JSON containing string `text`. Audio must be nonempty and no larger than 4 MiB or the smaller configured bound. Returned text is bounded to 65,536 characters and remains external evidence.

If the uploaded source library says the audio is unsupported for text extraction, that does not mean the separate transcription service cannot accept it. The media adapter uses the original artifact bytes rather than pretending the source extractor decoded audio.

## Worked example: generate spoken audio

Configure **Text to speech**, select the model and service-supported voice, grant **Generate spoken audio**, and test. Ask:

> Use the speech connection to read this exact sentence: “The maintenance note is ready for review.” Return the generated audio artifact.

The `speak` tool accepts `text` bounded to 4,096 characters. It posts `model`, `input`, `voice`, and `response_format:"wav"` to `/audio/speech`. The default voice string is `alloy` when no voice is configured; the remote service must actually support the selected value. The returned bytes must have a WAV header and are stored as an audio artifact.

## Worked example: generate one image

Configure **Image generation**, grant **Generate images**, and test its model catalog. Request a small illustrative diagram with a clear prompt. The `generate` tool accepts `prompt` up to 4,096 characters and posts to `/images/generations` with `n:1` and `response_format:"b64_json"`.

The result must contain exactly one bounded base64 PNG. Sturgis stores the validated bytes and returns an artifact ID, media type, and size. It does not support an arbitrary remote download URL or every image format through this adapter.

## Recovery, permissions, and downloads

Media operations follow connection grants and [action permissions](../settings/permissions.md). A successful catalog probe is not proof a later paid operation will succeed or what it will cost. Missing usage is not zero cost. Network failures after submission can leave effects unknown, so inspect the original invocation before resubmitting.

Use result artifact links or `artifacts.read` with `id`, optional `offset`, and `limit`. The direct authenticated download route is bounded to 4 MiB; use ranges for larger artifacts. Wrong-agent IDs remain unauthorized. If a response has an invalid WAV/PNG/JSON shape, configure a compatible service rather than reclassifying arbitrary bytes as a valid media result.

## Command examples

These are command-specific examples, not a script to run every operation. Replace uppercase IDs, example endpoints, timestamps, and revision numbers with the actual intended values. Save one JSON object as `params.json`, then run `sturgis command AGENT_ID METHOD --params-file params.json`. Omit the file for `{}`. If acknowledgement is unknown, keep request and domain operation identities unchanged. For an acknowledged unavailable result, follow the feature-specific recovery instructions.

| Method | Example parameters or private-prompt command |
| --- | --- |
| `artifacts.read` | `{"id":"ARTIFACT_ID","offset":0,"limit":4096}` |
