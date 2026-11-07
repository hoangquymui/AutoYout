---
name: AutoYout
description: "The Obsidian Command Center - High-throughput YouTube video dubbing & automation workstation"
colors:
  primary: "#0EA5E9"
  primary-hover: "#38BDF8"
  primary-pressed: "#0284C7"
  primary-border: "#38BDF8"
  accent-soft: "#38BDF8"
  run-from: "#16A34A"
  run-mid: "#10B981"
  run-to: "#0D9488"
  run-border: "#2DD4BF"
  pause-bg: "#261E0A"
  pause-border: "#664C0D"
  pause-text: "#FCD34D"
  stop-bg: "#2B1114"
  stop-border: "#6E262E"
  stop-text: "#FCA5A5"
  success: "#34D399"
  warning: "#FBBF24"
  danger: "#F87171"
  bg: "#060A12"
  surface: "#0C1424"
  surface-alt: "#09101C"
  surface-soft: "#080E1A"
  surface-raised: "#101B30"
  header-from: "#0E182B"
  header-to: "#091120"
  input-bg: "#070E1A"
  border: "#16253D"
  border-soft: "#121E33"
  border-strong: "#223B63"
  border-card: "#1B2D4A"
  text-strong: "#F4F8FE"
  text-title: "#EFF5FD"
  text-body: "#D1E1F5"
  text-muted: "#8FAAC9"
  text-subtle: "#7A96B8"
  text-dim: "#6681A3"
typography:
  display:
    fontFamily: "Segoe UI, Noto Sans, sans-serif"
    fontSize: "18pt"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "Segoe UI, Noto Sans, sans-serif"
    fontSize: "14pt"
    fontWeight: 700
    lineHeight: 1.3
  title:
    fontFamily: "Segoe UI, Noto Sans, sans-serif"
    fontSize: "11pt"
    fontWeight: 600
    lineHeight: 1.35
  body:
    fontFamily: "Segoe UI, Noto Sans, sans-serif"
    fontSize: "10pt"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Segoe UI, Noto Sans, sans-serif"
    fontSize: "8pt"
    fontWeight: 600
    lineHeight: 1.2
  code:
    fontFamily: "Cascadia Mono, Consolas, monospace"
    fontSize: "9pt"
    fontWeight: 500
    lineHeight: 1.4
rounded:
  xs: "2px"
  sm: "4px"
  md: "6px"
  base: "8px"
  lg: "10px"
  xl: "12px"
  2xl: "14px"
  3xl: "16px"
spacing:
  2xs: "2px"
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  2xl: "24px"
  3xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.primary-pressed}"
    textColor: "{colors.text-strong}"
    rounded: "{rounded.base}"
    padding: "7px 14px"
  button-primary-hover:
    backgroundColor: "{colors.primary}"
  button-standard:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text-body}"
    rounded: "{rounded.base}"
    padding: "7px 14px"
  button-run:
    backgroundColor: "{colors.run-from}"
    textColor: "#F0FDF4"
    rounded: "{rounded.base}"
    padding: "9px 10px"
  input-field:
    backgroundColor: "{colors.input-bg}"
    textColor: "{colors.text-body}"
    rounded: "{rounded.base}"
    padding: "5px 9px"
  card-panel:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.2xl}"
    padding: "16px"
  badge-chip:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.accent-soft}"
    rounded: "{rounded.lg}"
    padding: "3px 10px"
---

# Design System: AutoYout

## Overview

**Creative North Star: "The Obsidian Command Center"**

AutoYout is built as a precision workstation for creators producing and localizing video at industrial scale. The interface channels the clarity, gravity, and control of a mission-critical broadcast master suite: an expanse of deep obsidian surfaces, crisp 1px structural framing, and focused cybernetic sky-blue illuminations. Every panel feels anchored and definitive, giving operators the confidence to manipulate multi-step pipelines without visual fatigue during marathon editing sessions.

The aesthetic blends utilitarian rigor with digital refinement. Data density is high yet never claustrophobic: modular toolbars, dedicated inspector rails, waveform analyzers, and synchronized multi-language streams align along a balanced visual grid. Secondary chrome quietly recedes into dark tones, allowing rich video layers and live synthesis statuses to seize focus naturally.

**Key Characteristics:**
- **Obsidian Depth:** Deep space slate (`#060A12` base, `#0C1424` surfaces) creates an immersive dark canvas that eliminates glare and eye strain.
- **Luminous Precision:** High-frequency interactions and active states ignite with Cyber Sky (`#0EA5E9`) and Icy Cyan (`#38BDF8`), forming instantaneous visual beacons.
- **Tactile Certainty:** Strict corner radii (16px outer shells down to 8px controls), crisp 1px micro-borders, and tactile hover/pressed reactions evoke physical studio hardware.
- **Workflow Continuity:** Dedicated status badges, gradient execution triggers, and synchronized language lanes guide operators seamlessly through transcript scraping, AI cleaning, multi-language dubbing, video compositing, and automated channel deployment.

---

## Colors

The AutoYout palette balances ultra-low-light obsidian surfaces against vibrant cybernetic accents and unmistakable semantic alerts.

### Primary & Accents
- **Cyber Sky** (`#0EA5E9`): Primary interactive accent. Applied to active tabs, focused input strokes, slider fill tracks, and selected canvas bounding controls.
- **Electric Cerulean** (`#0284C7`): Grounded primary button background and deep accent anchors.
- **Icy Cyan** (`#38BDF8`): High-clarity hover states, active stage labels, waveform bars, and glowing border highlights.

### Functional & Semantic
- **Pipeline Emerald** (`#16A34A` -> `#10B981` -> `#0D9488`): Multi-stop gradient reserved exclusively for execution triggers (`Thực hiện toàn bộ`, `Chạy quy trình`) and successful terminal tasks.
- **Amber Beacon** (`#FBBF24` / `#FCD34D`): Paused operational states, active upload warning indicators, and cautionary notices.
- **Signal Coral** (`#F87171` / `#FB7185`): Emergency aborts (`Dừng hẳn`), critical pipeline faults, and destructive layer deletions.

### Neutral & Surface Hierarchy
- **Obsidian Void** (`#060A12`): Root window and canvas backdrop; absolute depth.
- **Deep Navy Surface** (`#0C1424`): Main card bodies, bottom docks, and workspace viewports.
- **Surface Alt / Soft** (`#09101C` / `#080E1A`): Recessed toolbars, input canvas wells, and secondary panels.
- **Raised Slate** (`#101B30`): Hover overlays, modal dialogs, and elevated button resting states.
- **Structural Borders** (`#16253D` default, `#1B2D4A` cards, `#223B63` emphasized): 1px hairline dividers defining architectural compartments.

### Text Hierarchy (Icy Blue-White)
- **Text Strong** (`#F4F8FE`): Window titles, high-priority metric numbers, active tab headers.
- **Text Body** (`#D1E1F5`): Primary readability layer for transcripts, file paths, and general UI labels.
- **Text Muted** (`#8FAAC9`): Explanatory secondary descriptions, inactive timestamps, and status hints.
- **Text Dim & Faint** (`#6681A3` / `#4B6485`): De-emphasized placeholder text, disabled actions, and collapsed item icons.

### Named Rules
**The Luminous Focus Rule.** The obsidian base canvas dominates ≥85% of visual area. Cyber Sky (`#0EA5E9`) and bright signal colors are strictly reserved for actionable controls, active indicators, and user-selected focus points. Their scarcity guarantees instant cognitive clarity.

---

## Typography

The typography pairs modern system sans-serif legibility with high-precision monospace numerals and timecodes.

**Display & Body Font:** `Segoe UI`, `Noto Sans`, system-ui, sans-serif  
**Code & Monospace Font:** `Cascadia Mono`, `Consolas`, monospace  

**Character:** Clean, technical, and unobtrusive. Letterforms feature crisp geometry, balanced aperture, and comfortable tracking across dense data tables.

### Hierarchy
- **Display** (`700`, `18pt / ~24px`, line-height `1.2`): Product branding (`AutoYout`) in the top navigation bar.
- **Headline** (`700`, `14pt / ~19px`, line-height `1.3`): Main module titles (`Tạo Video`, `Lồng tiếng & TTS`, `Kịch bản`).
- **Title** (`600`, `11pt / ~15px`, line-height `1.35`): Section and group box headers (`Khởi chạy nhanh quy trình`, `Quy trình tự động hóa`).
- **Body** (`400`, `10pt / ~13px`, line-height `1.45`): Standard input values, table cells, parameter labels, and button copy.
- **Caption / Label** (`600-700`, `7.5pt - 8.5pt`, uppercase / letter-spacing `0.02em`): Subtitle tags, badge labels (`STAGE CHIP`, `TIME REMAINING`), metadata captions.
- **Monospace Code** (`500`, `9pt`, line-height `1.4`): Frame timecodes (`00:01:24.120`), terminal logs, FFmpeg parameters, API key hashes.

### Named Rules
**The Timecode Monospace Rule.** Any timestamp, audio duration, frame position, or log output must render exclusively in Cascadia Mono/Consolas to prevent horizontal jitter during real-time playback updates.

---

## Layout

AutoYout utilizes a high-density, modular workstation layout split into dedicated functional zones:

1. **Persistent Header:** 16px rounded shell (`#0E182B` -> `#091120` gradient) containing branding, active project selector, global status badge, and theme/mode toggle.
2. **Main Tabbed Workspace:** Full-viewport tab navigation dividing the pipeline into discrete stages:
   - *Dự án (Project)*
   - *Kịch bản (Script)*
   - *Lồng tiếng (Dubbing / TTS)*
   - *Khung hình (Video Frame & Layers)*
   - *Tạo Video (Video Pipeline)*
   - *Thumbnail (AI Cover Generator)*
   - *Tự động (End-to-End Automation)*
   - *Đăng YouTube (Channel Multi-Account Deployer)*
   - *Cài đặt (Global Settings)*
3. **Split Master/Detail Rails:** 
   - Left side: Layer lists, file libraries, preset drawers, or step trackers.
   - Center: Interactive canvas preview, video player, or rich text transcript editors.
   - Right side: Parameter inspector (Transform, Scale, Color, Audio Wave settings).
4. **Docked Diagnostics Footer:** Real-time log console, multi-task progress bar, and emergency process abort keys.

**Spacing Rhythm:** 8px base grid (`8px`, `12px`, `16px`, `24px`). Panel insets default to `12px` to `16px`, while control gap spacing maintains a tight `6px` to `8px` for maximum utility per square inch.

---

## Elevation & Depth

AutoYout rejects heavy, blurry drop shadows in favor of **structural tonal layering** and **luminescent edge definition**.

Surfaces are distinguished by tonal contrast:
- Base Window: `#060A12`
- Recessed Inset: `#070E1A`
- Workspace Card: `#0C1424`
- Elevated Control / Popover: `#101B30`

Every elevation shift is accentuated by a crisp `1px solid` border (`#16253D` to `#223B63`). When an element is hovered or focused, depth is conveyed through border lighting (shifting to `#0EA5E9` or `#38BDF8`) rather than artificial z-axis casting.

### Named Rules
**The Flat-By-Default Rule.** All surfaces sit flat in rest state. Dimensional feedback is communicated exclusively through surface lightening (`+4%` luminance), high-contrast borders, and neon active rings.

---

## Shapes

The design system enforces a calibrated radius scale that reflects structural hierarchy:

- **16px (`rounded-3xl`):** Primary window enclosures, Header, and BottomPanel.
- **14px (`rounded-2xl`):** TabWidget central viewport panes and EditorPanel containers.
- **12px (`rounded-xl`):** Parameter GroupBoxes, Data Tables, File Panels, and Toolbar docks.
- **10px (`rounded-lg`):** Project Cards, Status Badges, Preset Thumbnails, and Log Terminal.
- **8px (`rounded-base`):** Interactive controls: PushButtons, LineEdits, ComboBoxes, SpinBoxes, Tab pills, and Menu popups.
- **6px (`rounded-md`):** Inline chips, Variable insert pills, and Sub-menu selection items.
- **4px - 5px (`rounded-sm`):** Checkbox indicators, Progress bars, and Scrollbar grab handles.
- **2px (`rounded-xs`):** Slider track grooves.

---

## Components

### Buttons
- **Shape:** `border-radius: 8px`, `border: 1px solid`.
- **Primary:** Background `#0284C7`, text `#FFFFFF`, border `#38BDF8`. Hover shifts to `#0EA5E9`. Pressed `#0369A1`.
- **Run Pipeline (Hero):** Gradient `qlineargradient(#16A34A, #10B981, #0D9488)`, text `#F0FDF4`, font-weight `800`, padding `9px 10px`.
- **Standard Action:** Background `#0F1C31`, text `#D1E2F7`, border `#1E3456`. Hover `#152745`, border `#2E4E80`.
- **Icon Button:** `26px x 26px` or `32px x 32px`, centered vector glyphs, background `#0E1A2E`, border `#1C2E4B`.
- **Variable Pill:** Pill button (`border-radius: 6px`, padding `2px 8px`, text `#38BDF8`, bg `#0E1F36`).

### Input Fields & Controls
- **Text & Number Inputs:** Background `#070E1A`, border `1px solid #1B2D4A`, text `#E4EEFB`. Focus triggers a glowing `#0EA5E9` border.
- **Combo Dropdowns:** Fixed-height inputs with custom down-chevron icon (`assets/icons/chevron-down.svg`). Popups render in elevated `#0A1322` with `padding: 4px` and rounded item selections.
- **SpinBoxes:** Custom integrated up/down stepper handles (`#0C1525`) separated by hairline borders.

### Cards & Group Boxes
- **Group Box:** Outlined by `1px solid #162740` with recessed `#0A1322` background. Group title floats in top-left with bold `#BCD2EC` text.
- **Status Badge:** Rounded pill with semantic dot (`#34D399` Success, `#38BDF8` Info, `#FBBF24` Warning, `#F87171` Error) and compact title-value layout.

### Canvas & Layer Stack (Video Frame)
- **Layer List:** Clean transparent container with rounded item bars (`padding: 7px 8px`, `border-radius: 8px`). Newly added or duplicated layers automatically insert at the top index (0) and highlight.
- **Active Selection:** Items render with `#132B4F` background and crisp cyan borders.

### Progress & Diagnostics
- **Progress Bar:** Slimline `9px` height, `#070E1B` channel with smooth cyan gradient chunk (`#0284C7` -> `#38BDF8`).
- **Terminal Log View:** Darkest well (`#040710`), Cascadia Mono font, timestamped coloring (Debug: `#5E7F9F`, Info: `#38BDF8`, Success: `#34D399`, Error: `#F87171`).

---

## Do's and Don'ts

### Do:
- **Do** maintain a strict 1px border on all floating containers and input fields to sustain architectural structure.
- **Do** position newly added assets or stickers directly at the top of the layer stack so they are instantly visible and manipulable.
- **Do** format all timecodes, durations, and byte sizes with fixed-width Cascadia Mono/Consolas fonts.
- **Do** use the triple-stop green gradient (`#16A34A` -> `#10B981` -> `#0D9488`) exclusively for major pipeline execution triggers.
- **Do** preserve full contrast and color pairing parity when switching between Dark Mode and Light Mode.

### Don't:
- **Don't** apply diffuse blur drop shadows; delineate depth purely through surface luminance steps and crisp 1px borders.
- **Don't** saturate greater than 15% of the total viewport with accent colors (`#0EA5E9` / `#38BDF8`).
- **Don't** use arbitrary corner radiuses; adhere strictly to the 16 / 14 / 12 / 10 / 8 / 6 / 4px scale.
- **Don't** hide or obscure critical error messages; display descriptive fault details in `#F87171` accompanied by icon feedback.
- **Don't** change component backgrounds on hover without simultaneously updating the border tone to provide tactile feedback.

