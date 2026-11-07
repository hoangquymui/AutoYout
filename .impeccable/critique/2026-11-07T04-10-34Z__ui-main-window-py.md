---
target_identity: "file:D:\\mui\\f375\\AutoYout\\ui\\main_window.py"
target_fingerprint: "sha256:37c65a33c323e67ac2a6884ffe8ffbb83da6775f5489dd44adcab02b022598b9"
target_path: "D:\\mui\\f375\\AutoYout\\ui\\main_window.py"
timestamp: 2026-11-07T04-10-34Z
slug: ui-main-window-py
closed: true
---
Method: dual-agent (A: 670450d3-ebb1-48fc-93ae-e1341d04c317 · B: e6550ecb-f4c8-45e6-8db4-8774a49536e0)

# Design Critique: AutoYout Desktop Workstation
**Target:** `ui/main_window.py` and AutoYout UI suite (`ui/tab_*.py`, `ui/theme.py`, `ui/styles.py`)  
**Design System Anchor:** *"The Obsidian Command Center"* (`DESIGN.md`, `PRODUCT.md`)

---

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|:-----:|-----------|
| 1 | **Visibility of System Status** | **3 / 4** | Header hardware badges and glowing node rings give rich local status; but switching away from AutomationTab leaves no global ETA or abort dock. |
| 2 | **Match System / Real World** | **3 / 4** | NLE canvas, safe margins, and audio visualizer match studio tools; but jargon collisions occur (English internal names like "Quick Input Bar", "Stage Chip", "cues" mixed with Vietnamese). |
| 3 | **User Control and Freedom** | **2 / 4** | Canvas has full undo/redo stack; but Steps 1 & 2 in Automation cannot be toggled off, and layer deletion lacks instant undo. |
| 4 | **Consistency and Standards** | **2 / 4** | Strict 440+ line token palette and 8-step radius scale in core styles; but Settings is placed as step #2 in a sequential pipeline, FrameTab is buried inside VideoTab, and 112+ hardcoded color bypasses exist in tabs. |
| 5 | **Error Prevention** | **2 / 4** | Directory validation before running; but **zero pre-flight review** before dispatching live video uploads to public YouTube channels. |
| 6 | **Recognition Rather Than Recall** | **2 / 4** | Description chips are clickable; but Frame inspector displays raw decimals (`0.04`, `0.78`) instead of pixels/percentages, and model combos dump 20+ cryptic IDs without speed/cost guidance. |
| 7 | **Flexibility and Efficiency** | **3 / 4** | Dual operation (1-click batch vs granular per-cue editing) and detachable floating console; but no batch prompt editor across languages and no shortcut quick-switcher. |
| 8 | **Aesthetic and Minimalist Design** | **3 / 4** | Atmospheric Obsidian dark theme with crisp 1px borders and disciplined cyber-sky accents; but Quick Input Bar in AutomationTab is excessively tall and dense. |
| 9 | **Error Recovery** | **2 / 4** | Live diagnostics in LogConsole; but failures truncate to a single status line with no "Copy Error Log" or jump-to-failing-file action. |
| 10 | **Help and Documentation** | **1 / 4** | Rich hover tooltips on 98% of controls; but completely lacks first-time onboarding (FFmpeg/API keys) or keyboard shortcuts cheat sheet. |
| **Total** | | **23 / 40** | **Functional / Notable Friction** |

---

## Design Specificity Verdict

**Verdict: Custom-Authored Studio Workstation with Notable Information Architecture Seams.**

- **LLM Assessment**: AutoYout achieves strong product-specific identity. The synthesis of an n8n-style automation graph with a multi-layer CapCut-grade canvas compositor directly embodies "The Obsidian Command Center". However, structural seams break the illusion: Settings is wedged between Project and Script, FrameTab is buried inside VideoTab, and multi-language controls are scattered across isolated tabs.
- **Deterministic Scan**:
  - The deterministic CLI detector reported **41 advisory findings** across Python UI files (34 `design-system-color`, 7 `design-system-radius`).
  - AST code audit revealed **112+ hardcoded hex colors** bypassing `ui/theme.py` (specifically in `tab_settings.py` [43], `tab_automation.py` [29], and `tab_frame.py` [19]).
  - 7 non-scale corner radii (`3px`, `5px`, `9px`) violate `DESIGN.md`'s 8-step scale.
  - **Dead code detected**: Orphan buttons `self.srt_browse` and `self.srt_clear` in `ui/tab_video.py:447-448` are instantiated but never added to any layout.
  - **Accessibility deficit**: 177 tooltips exist, but `setAccessibleDescription` is 0% and ad-hoc icon buttons in `tab_script.py` and `tab_thumbnail.py` lack `setAccessibleName`.
- **Visual Overlays**: Not applicable. AutoYout is a native PySide6/Qt desktop app rendering via native window surfaces (`HWND`/`QPainter`), without a Chromium/DOM environment.

---

## Overall Impression

AutoYout is an ambitious, high-craft creative automation suite with exceptional visual presence in its primary dark theme. The canvas editor and animated pipeline cards look and feel like high-end broadcast hardware. However, the experience suffers from an inverted tab lifecycle, cognitive overload in inspector panels, unguided external YouTube publishing, and widespread color token drift in secondary tabs.

---

## What's Working

1. **Atmospheric Obsidian Visual Foundation**: The deep space obsidian palette (`#060A12`, `#0C1424`), crisp 1px hairline framing, and disciplined Cyber Sky highlights (`#0EA5E9`) create an immersive, distraction-free environment that prevents eye fatigue.
2. **Dynamic 5-Node Automation Graph**: In `ui/tab_automation.py`, the horizontal workflow pipeline featuring custom input/output pins and real-time sinusoidal pulsing halos provides immediate tactile feedback and high clarity during batch execution.
3. **High-Performance Multi-Layer Compositor (`FrameCanvas`)**: Real-time sine-wave audio visualizers, broadcast-safe zone guidelines, aspect-ratio shifting (16:9, 9:16, 1:1, 4:5), layer z-ordering, and full undo/redo stack represent outstanding visual engineering.

---

## Priority Issues

### [P0] Missing Pre-Flight Deployment Review for YouTube Uploads
- **Why it matters**: Publishing to YouTube is an irreversible external action. Clicking "Thực hiện toàn bộ" with credentials active will publish live videos to public channels without giving the operator a final chance to verify video previews, titles, descriptions, and privacy visibility (Public vs. Unlisted).
- **Fix**: Insert a modal `PreFlightUploadDialog` before the upload worker starts, displaying a structured review table: `[Thumbnail] [Title] [Target Channel] [Privacy: Unlisted/Private/Public] [Schedule]`, requiring explicit confirmation.
- **Suggested command**: `/impeccable harden ui/tab_youtube.py`

### [P1] Disjointed Information Architecture & Inverted Tab Order
- **Why it matters**: Main tabs currently read: `Dự án -> Cài đặt -> Kịch bản -> Lồng tiếng -> Video -> Thumbnail -> YouTube -> Automation`. Settings is placed at Tab 2, while the Automation Command Center is relegated to Tab 8, and the Frame compositor is hidden inside VideoTab. This contradicts the sequential manufacturing metaphor.
- **Fix**: Move `Cài đặt` out of the pipeline tabs to a permanent header gear icon. Reorder tabs to reflect the real lifecycle: `Automation (Command Center) -> Dự án -> Kịch bản -> Lồng tiếng -> Khung hình (Frame) -> Video -> Thumbnail -> YouTube`. Promote `FrameTab` to a top-level tab.
- **Suggested command**: `/impeccable layout ui/main_window.py`

### [P1] Automation Steps 1 & 2 Are Hardcoded Non-Toggleable
- **Why it matters**: Creators who already have their own human-translated scripts or studio-recorded voiceover files cannot run an automated Video + Thumbnail + Upload pipeline because steps 1 and 2 cannot be unchecked.
- **Fix**: Set `toggleable=True` on all 5 workflow cards. When Step 1 or 2 is toggled off, verify that corresponding project folders contain existing audio/text files before launching.
- **Suggested command**: `/impeccable harden ui/tab_automation.py`

### [P2] Blind Navigation During Multi-Minute Pipeline Runs
- **Why it matters**: While rendering or downloading, progress exists only within Tab 8 (Automation). Switching to inspect scripts or canvas leaves no global indication of pipeline state, causing anxiety and repeated tab flipping.
- **Fix**: Add a persistent mini-dock to `MainWindow._build_header`: `[⚡ Đang tạo video: 64%] [Tạm dừng] [Dừng hẳn]`, visible only when `self.worker is not None`.
- **Suggested command**: `/impeccable polish ui/main_window.py`

### [P2] Widespread Color Token Drift in Secondary Tabs
- **Why it matters**: Over 112 hardcoded hex colors and 34 advisory findings bypass `ui/theme.py` (e.g. raw `#64748B`, `#EF4444`, `#22C55E`), causing elements to break contrast when toggling to Light Mode.
- **Fix**: Replace all hardcoded hex values with semantic tokens from `ui/theme.py` (`theme.TEXT_MUTED`, `theme.BORDER`, `theme.DANGER`, `theme.SUCCESS`, `theme.STATE_COLORS`).
- **Suggested command**: `/impeccable distill ui/tab_settings.py`

---

## Persona Red Flags

- **Alex (High-Volume Studio Lead - 15 Channels, 20 Videos/Day)**: In `tab_youtube.py`, channel language assignments require opening a modal dialog for each channel one-by-one instead of a matrix view. A quota exhaustion error truncates to 1 line with no fast secret hot-swap.
- **Jordan (Solo Creator First-Timer)**: Launches the app to red dependency warning badges (`FFmpeg: Không chạy được`). Tab 2 is "Cài đặt" with technical path pickers. Jordan faces immediate technical intimidation with no guided 1-click wizard.
- **Mei (International Localizer - Nuanced Prompts)**: Customizing translation prompts per language requires digging into two nested modal dialogs (`_ManageLanguagesDialog` -> `_PromptEditDialog`) for each language separately, with no side-by-side split comparison against the source transcript.

---

## Minor Observations

1. **Dead Code Orphan Buttons**: `self.srt_browse` and `self.srt_clear` in `ui/tab_video.py:447-448` are never added to any layout or given text/icons.
2. **Duplicate Instance Risk**: `ThumbnailPanel` is instantiated twice in memory (`MainWindow` and `YouTubeConfigTab`).
3. **Language Flag Misalignment**: Using country flags (🇺🇸 for English, 🇧🇷 for Portuguese) creates localization friction; ISO code pills (`EN`, `VI`, `JA`) are standard.
4. **Radius Scale Drift**: Radii of `3px`, `5px`, and `9px` in `styles.py` and `tab_frame.py` should snap to `4px`, `6px`, and `8px/10px`.
5. **Screen Reader Accessibility**: Ad-hoc icon buttons in `tab_script.py` and `tab_thumbnail.py` lack `setAccessibleName`.

---

## Questions to Consider

1. **Operator Mode vs. Director Mode**: Should AutoYout offer a toggle between an "Operator Mode" (focused on high-throughput batch automation) and a "Director Mode" (dedicated to canvas framing, cue editing, and prompt tuning)?
2. **Asynchronous Publishing Queue**: Should YouTube publishing be decoupled from local video compositing so that quota limits or network outages don't halt offline video generation?
3. **Unified Multi-Language Master Swimlane**: What if the project displayed a horizontal timeline matrix where each language lane showed live progress chips across all 5 stages simultaneously?
