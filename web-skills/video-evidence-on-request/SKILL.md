---
name: video-evidence-on-request
description: Use only for explicit requests for “视频证据/视频核验/结合视频” or “video evidence/verification,” including analysis of a supplied YouTube or Bilibili URL. Do not invoke for ordinary web research or incidental video mentions. Search and read with the connected Video-evidence tools before answering.
---

# Video Evidence on Request

Collect evidence first; infer and answer only after collection finishes.

1. Invoke only when the user explicitly asks to use, find, analyze, or verify video evidence, or asks about the contents of a supplied YouTube/Bilibili video. The user need not mention `@Video-evidence`. Do not invoke for generic research, writing, or a merely incidental mention of video.
2. Start ordinary web research and `search_videos` in the same evidence-gathering phase when parallel calls are available. Use the connected Video-evidence MCP/app tools; never substitute search snippets for video contents.
3. Analyze at most three directly relevant public videos with `start_video_analysis`; use `quick` by default and `deep` for demonstrations, interfaces, charts, field footage, disputed claims, or visual/speech conflicts. Poll `get_video_analysis` to a terminal state. Use `get_video_window` for each important timestamp, visual claim, uncertain name/number, or transcript/visual mismatch.
4. Do not synthesize conclusions or issue the final answer until all selected video jobs/windows and required web sources have completed or reached a terminal failure. Check both transcript and relevant frames for important video-based claims.
5. Distinguish what a video says, what its frames visibly show, and what independent web sources verify. Cite title, channel, publication date, URL, and timestamp/range. Omit routine retrieval and polling details.

If Video-evidence is missing, disconnected, or unusable, stop video calls after one reasonable retry or alternate public result. Continue with web research only and explicitly state that video evidence could not be checked and why. Never imply that an inaccessible video was watched. If only some analyses succeed, label the video evidence as partial.
