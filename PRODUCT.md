# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Primary Audience:** YouTube Content Creators and Media Studios producing content for international audiences.
- **Situation:** Operating a multi-language content expansion strategy where an original master video (or transcript) must be localized into multiple target languages (e.g., Vietnamese, English, Japanese, Chinese, Thai, etc.) and distributed to designated language-specific YouTube channels.
- **Core Job:** Eliminate repetitive manual transcription, translation, voice synthesis, video compositing, thumbnail design, and multi-account publishing through a unified, automated, local-first workstation.

## Product Purpose

AutoYout exists to transform single-language video content into a scalable, multi-language global publishing pipeline. Success means an operator can provide a YouTube link or local source file, select target languages, and reliably produce high-quality, fully dubbed, branded, and uploaded localized videos with minimal manual intervention.

## Positioning

Unlike web-based SaaS video editors that impose subscription paywalls, slow cloud rendering queues, privacy risks for proprietary transcripts/API keys, and rigid templates, AutoYout is a **local-first desktop workstation** (PySide6/Qt) that combines:
1. Direct, secure use of the user's own AI API keys (Google Gemini, OpenAI, ElevenLabs, Edge-TTS, FPT AI).
2. Deep, multi-layered visual video framing (dynamic waveforms, subtitle stylers, customizable sticker/image overlays with full z-ordering) rendered locally via high-performance FFmpeg.
3. Native multi-account YouTube OAuth routing with per-language channel publishing and language-partitioned asset management.

## Operating Context

- **Environment:** Windows / macOS / Linux desktop workstation running PySide6 and FFmpeg.
- **Workflow Pipeline:**
  1. *Project & Asset Setup:* Language-structured filesystem hierarchy (`scripts/<lang>`, `audio/<lang>`, `video/<lang>`, `thumbs/<lang>`).
  2. *Script Ingestion:* YouTube transcript extraction via yt-dlp, AI text normalization, and multi-lingual translation.
  3. *Voice Synthesis (TTS & Dubbing):* Sentence-aligned voiceover synthesis with fine-tuned voices per language.
  4. *Frame Layout & Visual Compositing:* Aspect-ratio framing (16:9, 9:16, 1:1, 4:5), audio wave visualizer, subtitle styling, and multi-layer sticker overlays.
  5. *Thumbnail Synthesis:* AI-driven multilingual thumbnail generator.
  6. *Channel Automation & Upload:* OAuth-authenticated YouTube uploading mapped to designated language channels with custom metadata and scheduling.
  7. *Diagnostics & Monitoring:* Real-time terminal log viewer and thread-safe pipeline controllers (Run, Pause, Stop).

## Capabilities and Constraints

- **Capabilities:**
  - Automated transcript fetching and AI rewriting.
  - Multi-provider TTS engine integration (Edge-TTS, Gemini, ElevenLabs, FPT AI).
  - Multi-layer visual canvas editor with realtime layer reordering (new layers place on top).
  - High-performance local video rendering with FFmpeg.
  - Granular step execution toggles in Automation tab (steps 3, 4, 5 toggleable with visual dimming).
  - YouTube multi-account credential management and language routing.
- **Constraints:**
  - Requires local FFmpeg installation on the host system.
  - Dependent on third-party AI provider API quotas and keys provided by the user.
  - Subject to YouTube Data API v3 upload quotas per Google Cloud project.

## Brand Commitments

- **Product Name:** AutoYout
- **Visual Identity:** "The Obsidian Command Center" (`DESIGN.md`). Dark-first, studio-grade aesthetic with Obsidian deep surfaces (`#060A12`, `#0C1424`), Cyber Sky accents (`#0EA5E9`), and Pipeline Emerald indicators (`#16A34A` -> `#10B981` -> `#0D9488`).
- **Tone:** Focused, technical, dependable, high-density, professional broadcast software.

## Evidence on Hand

- Fully functional codebase in `AutoYout/` featuring core modules: `ProjectTab`, `ScriptTab`, `DubbingTab`, `FrameTab`, `VideoTab`, `ThumbnailTab`, `AutomationTab`, `YouTubeTab`, and `SettingsTab`.
- Comprehensive design tokens and global stylesheet in `ui/theme.py` and `ui/styles.py`.
- Automated test suite covering 247 pytest cases across pipeline, GUI, frame layout, and multi-language routing (`tests/`).

## Product Principles

1. **Local-First & Data Sovereignty:** All source assets, intermediate scripts, audio tracks, rendered videos, and API credentials remain stored locally on the user's filesystem in transparent directory structures.
2. **End-to-End Automation with Granular Manual Override:** The pipeline can run fully hands-free from link to upload, but operators always retain the ability to inspect, tweak, or re-run any individual node (script, audio, frame, thumbnail, or upload) at any time.
3. **Layer Transparency & Visual Certainty:** Video frame elements behave predictably (new assets always arrive on top of the stack, z-ordering is fully customizable, and preview matches final FFmpeg output).
4. **Resilient Multilingual Parity:** Adding a new language seamlessly creates corresponding project directories, TTS queues, thumbnail prompts, and channel upload targets without disturbing existing language assets.
