---
name: video-evidence-research
description: Research with timestamped transcript and visual evidence from public YouTube and Bilibili videos. Use for every explicit video search, summary, verification, demonstration, interface, chart, speech, field-footage, or video-claim task. Also invoke automatically during ordinary substantive external-information research whenever relevant video could improve accuracy; do not invoke for editing, rewriting, casual conversation, or tasks that need no external evidence.
---

# Video Evidence Research

Use the `video-evidence` MCP server to add compact, source-aware video evidence to research without turning the answer into a search log.

## Research workflow

1. For every substantive external research task, call `search_videos` at least once with a focused query. Skip video tools only when the task is non-research work such as rewriting supplied text.
2. Inspect verified metadata. If directly relevant public videos exist, analyze one to three. Prefer primary recordings, original demonstrations, official channels, and near-contemporaneous footage.
3. Choose `quick` for triage, `standard` for ordinary explainers or talks, and `deep` for demonstrations, interfaces, charts, field footage, or disputed claims.
4. Poll `get_video_analysis` until complete. If anonymous access returns `authentication_required` or another access limitation, select another public video. Never attempt login, CAPTCHA, age-gate, paywall, or private-content bypass.
5. For every important conclusion, check both the relevant transcript and the contact sheet. Call `get_video_window` around the cited time when a name, number, chart, interface state, physical action, edit, or transcript/visual mismatch matters.
6. For current, disputed, or high-impact conclusions, require at least two independent sources. Prefer an original video plus an authoritative text source. Do not treat two reposts or commentary about the same recording as independent.
7. If MCP search or analysis fails, continue normal web research and state the video-access limitation briefly.

## Evidence rules

- Cite the video title, channel, publication date, URL, and timestamp or bounded time range.
- Write “the video says/claims” for spoken assertions and “the frame shows” only for directly visible details. Write “external source verifies” only after independent corroboration.
- Treat automatic captions, ASR, and OCR as fallible. Cross-check names and numbers against audio context, neighboring transcript, visual text, and an independent source where consequential.
- If speech and visible evidence differ, report the discrepancy rather than silently choosing one.
- Do not infer unseen events between sampled frames. Preserve the returned uncertainty and access/analysis limitations.
- Summarize findings and evidence; omit routine polling and retrieval steps from the final answer.
