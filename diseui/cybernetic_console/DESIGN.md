---
name: Cybernetic Console
colors:
  surface: '#0f131d'
  surface-dim: '#0f131d'
  surface-bright: '#353944'
  surface-container-lowest: '#0a0e18'
  surface-container-low: '#171b26'
  surface-container: '#1c1f2a'
  surface-container-high: '#262a35'
  surface-container-highest: '#313540'
  on-surface: '#dfe2f1'
  on-surface-variant: '#c7c4d7'
  inverse-surface: '#dfe2f1'
  inverse-on-surface: '#2c303b'
  outline: '#908fa0'
  outline-variant: '#464554'
  surface-tint: '#c0c1ff'
  primary: '#c0c1ff'
  on-primary: '#1000a9'
  primary-container: '#8083ff'
  on-primary-container: '#0d0096'
  inverse-primary: '#494bd6'
  secondary: '#4edea3'
  on-secondary: '#003824'
  secondary-container: '#00a572'
  on-secondary-container: '#00311f'
  tertiary: '#ffb95f'
  on-tertiary: '#472a00'
  tertiary-container: '#ca8100'
  on-tertiary-container: '#3e2400'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e1e0ff'
  primary-fixed-dim: '#c0c1ff'
  on-primary-fixed: '#07006c'
  on-primary-fixed-variant: '#2f2ebe'
  secondary-fixed: '#6ffbbe'
  secondary-fixed-dim: '#4edea3'
  on-secondary-fixed: '#002113'
  on-secondary-fixed-variant: '#005236'
  tertiary-fixed: '#ffddb8'
  tertiary-fixed-dim: '#ffb95f'
  on-tertiary-fixed: '#2a1700'
  on-tertiary-fixed-variant: '#653e00'
  background: '#0f131d'
  on-background: '#dfe2f1'
  surface-variant: '#313540'
typography:
  headline-lg:
    fontFamily: Geist
    fontSize: 1.75rem
    fontWeight: '600'
    lineHeight: 2.25rem
    letterSpacing: -0.025em
  headline-md:
    fontFamily: Geist
    fontSize: 1.25rem
    fontWeight: '600'
    lineHeight: 1.75rem
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Geist
    fontSize: 1rem
    fontWeight: '600'
    lineHeight: 1.5rem
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Geist
    fontSize: 1rem
    fontWeight: '400'
    lineHeight: 1.5rem
  body-md:
    fontFamily: Geist
    fontSize: 0.875rem
    fontWeight: '400'
    lineHeight: 1.25rem
  body-sm:
    fontFamily: Geist
    fontSize: 0.75rem
    fontWeight: '400'
    lineHeight: 1rem
  code-md:
    fontFamily: JetBrains Mono
    fontSize: 0.8125rem
    fontWeight: '400'
    lineHeight: 1.25rem
  code-sm:
    fontFamily: JetBrains Mono
    fontSize: 0.6875rem
    fontWeight: '500'
    lineHeight: 0.875rem
  badge-label:
    fontFamily: JetBrains Mono
    fontSize: 0.6875rem
    fontWeight: '600'
    lineHeight: 0.75rem
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1rem
  gutter-lg: 1.5rem
  margin: 1.25rem
  margin-lg: 2rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system targets technical operators, AI engineers, and automation power users managing localized device fleets, computer vision workflows, and on-device machine learning loops. The atmosphere is nocturnal, surgical, high-density, and calm under high throughput.

The visual direction combines **Technical Minimalism** with **Controlled Glassmorphism**:
- Deep, low-reflectance carbon backplates remove visual noise during prolonged continuous observation.
- High-contrast, micro-metric status elements provide rapid glanceability into ADB connections, local LLM runtimes (e.g., Ollama), and real-time screen ingestion pipelines.
- Glass panels with subtle structural borders isolate heavy functional units (device mirror streams vs. automation step builders) while maintaining a cohesive, unified desktop cockpit.

## Colors

The palette relies on deep cold dark foundations accented by precise, luminescent status hues:

- **Base Canvas (`#0b0f19`)**: Deep slate obsidian canvas delivering extreme contrast for mirrors and code streams.
- **Surface Elevation 1 (`#111827`)**: Primary card panels, workspace segments, and dashboard modules.
- **Surface Elevation 2 (`#1f2937`)**: Sub-sections, grouped inputs, pill containers, and toolbars.
- **Borders & Dividers (`#374151`)**: Razor-sharp perimeter boundaries that delineate active contexts without loud color contrast.
- **Primary Indigo (`#6366f1`)**: Active interactive focus, operational workflows, and primary CTA triggers.
- **Telemetry Emerald (`#10b981`)**: Positive telemetry, active ADB connection states, live video stream pings, and ready runtimes.
- **Warning Amber (`#f59e0b`) & Error Rose (`#f43f5e`)**: Pipeline interruptions, disconnected devices, or automated error mitigations.
- **Monospace Slate (`#9ca3af` to `#e5e7eb`)**: Crisp alphanumeric rendering for ADB shell traces, bundle identifiers, and parameter controls.

## Typography

Typography prioritizes micro-legibility and functional data density:

- **Geist** handles the primary interface structure, headers, and UI controls. Its geometric clarity retains legibility under low-saturation dark backgrounds.
- **JetBrains Mono** is reserved for raw metrics, package names (e.g., `com.instagram.lite`), ADB identifiers, terminal streams, and hardware status indicators.
- Display sizes remain compact: top-level headings do not exceed `1.75rem` to prioritize screen real estate for device viewports, inspection panels, and script execution steps.

## Layout & Spacing

This layout uses an asymmetrical desktop-first split console:
- **Primary Left Column (Mirror & Fleet Status)**: Sticky 40% width viewport anchored to the real-time scrcpy/ADB frame stream, peripheral toggles, and device health monitors.
- **Secondary Right Column (Orchestration Engine)**: Fluid 60% workspace with tabbed multi-step automation pipelines, cycle controllers, and contextual log inspection.

Padding density follows a compact 4px baseline rhythm (`space-xs` = 4px, `space-sm` = 8px, `space-md` = 12px, `space-lg` = 16px, `space-xl` = 24px) ensuring control clusters fit inside high-resolution desktop windows without vertical overflow.

## Elevation & Depth

Visual hierarchy uses precise border contrasts and layered tonal depth rather than prominent drop shadows:

- **Level 0 (Canvas Base)**: `#0b0f19` pure background.
- **Level 1 (Structural Cards)**: `#111827` paired with a 1px solid `#374151` border.
- **Level 2 (Active Control Well)**: `#1f2937` with inset highlights (`box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05)`).
- **Glass Accents**: Used on top telemetry bars and device control overlays via `backdrop-filter: blur(12px)` over a 70% alpha `#111827b3` fill.
- **Focus / Glow States**: Interactive elements emit subtle primary indigo or emerald halos (`0 0 15px -3px rgba(99, 102, 241, 0.25)` or `rgba(16, 185, 129, 0.25)`), avoiding muddy spreads.

## Shapes

The interface implements a tight **Soft (`1`)** radius profile:
- Primary panels and dashboard modules use `0.5rem` (`rounded-lg`).
- Form elements, sub-cards, nested controls, and inline buttons use `0.25rem` (`rounded`).
- Telemetry indicators, device state pills, and status markers use full pill geometries (`rounded-full`) to contrast against the architectural cards.

## Components

### Buttons & Interactive Triggers
- **Primary Action**: Background `#6366f1`, text `#ffffff`, border 1px solid `#818cf8`. Hover states elevate brightness slightly with a restrained glow.
- **Secondary Action**: Background `#1f2937`, text `#e5e7eb`, border 1px solid `#374151`. Hover initiates border transition to `#4b5563`.
- **Destructive / Stop**: Background `#1f2937`, text `#f87171`, border 1px solid `#ef444433`. Hover darkens the surface with red hue tint.

### Status Indicators & Badges
- Built using `JetBrains Mono` at `badge-label`.
- Composed of an optional glowing micro-dot (6px) followed by uppercase status text.
- Connected/Active states: `#10b9811a` background, `#34d399` text, `#10b98133` border.
- Disconnected/Error states: `#f43f5e1a` background, `#fb7185` text, `#f43f5e33` border.
- Standby/AI model ready: `#6366f11a` background, `#a5b4fc` text, `#6366f133` border.

### Input Fields & Parameter Steppers
- Input backgrounds are recessed using `#0b0f19` or `#111827` with a 1px `#374151` boundary.
- Focused inputs swap the border to `#6366f1` with zero layout shift.
- Inline numeric cycle steppers combine minimal segmented blocks (`-` / value / `+`) in monospaced format.

### Navigation & Automation Module Tabs
- Enclosed inside an elevation-2 track (`#1f2937`).
- Active tab carries an elevated dark slate pill (`#111827`) with bright white font and an optional accent top-line or indicator icon.
- Inactive tabs maintain `#9ca3af` text with no border.

### Device Mirror Panel
- Bordered container with black-level fill (`#030712`) framing the live stream.
- Corner overlays host real-time stream status, FPS metrics, and capture controls using semi-translucent glass tags.