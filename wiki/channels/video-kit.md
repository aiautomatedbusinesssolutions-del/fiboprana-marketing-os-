---
id: channels/video-kit
title: Video kit — the faceless production chain
tags: [channels, video, production]
consumers: [repurposer, content_pull]
canonical: false
source_of_truth: ../../Fiboprana Marketing/content/PIPELINE.md
last_verified: 2026-08-26
---

Production lives in the sibling repo `Fiboprana Marketing/` — fully scripted, zero manual
editing (proven end-to-end on video 1 for ~$7 of generation):

worksheet (`scripts/new-video.mjs`) → narrate (`narrate.mjs`, ElevenLabs own-voice clone)
→ stills (`stills.mjs`, fal.ai Flux + Quiet Earth style tail) → animate (`animate.mjs`,
Seedance 1.5 Pro; 5s default, 10s for long beats) → transcribe (`transcribe.mjs`, fal
Whisper word timestamps) → timeline (`timeline.mjs`) → shorts (`shorts-timeline.mjs`) →
render (Remotion: `Master` 1920×1080\@30 + three 9:16 Shorts; karaoke captions with gold
active word, Fraunces hook card, CTA card, music bed ≈ −20dB) → measure
(`content-stats.mjs`, day-7/28 YouTube stats).

Each stage runs `node --env-file=.env.local scripts/<x>.mjs` from that repo. Per-step
outcomes, costs, and friction are logged to `content/production-log.jsonl` — the
automation-discovery ledger this OS's video stage reads. Binding rules ride with
[[brand/visual-identity]] (pacing: every beat moves, ≤12s holds, no repeated clips) and
the PIPELINE.md pre-publish checklist (claims verified, compliance pass, disclosure,
tracked link).

<!-- human -->

## Provenance

This page is the *interface* between the marketing OS and the production sub-workflow:
where the engine's original design had "founder records on camera," Fiboprana plugs in
this chain. The OS's video modules (`videos/silence_cut.py`, `videos/transcribe.py`) are
for footage-based work and mostly idle here; the fal/Remotion chain in the sibling repo is
the real producer. Keep it separable — that's the boilerplate seam.
