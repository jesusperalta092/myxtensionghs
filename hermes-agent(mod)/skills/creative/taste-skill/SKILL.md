---
name: taste-skill
description: Anti-slop frontend design taste engine by Leon Lin (@Leonxlnx). Enforces non-generic aesthetic direction, design variance calibration, intentional color palettes, and audience-first layout personality.
version: 2.0.0
author: Leon Lin / Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [design, taste, anti-slop, frontend, creative, ui, direction, aesthetics]
    related_skills: [emil-motion, impeccable-design, claude-design, popular-web-designs]
---

# Taste Skill: Anti-Slop Frontend Aesthetics

Use this skill when designing, building, or refactoring user interfaces, landing pages, web applications, dashboards, or interactive components. This skill directly counteracts the generic, predictable "AI slop" aesthetic by enforcing human design intentionality, aesthetic judgment, and tailored visual hierarchy.

## 1. The Three Master Dials

Before writing any CSS or layout code, calibrate these three dials based on the user's project:

### Dial 1: DESIGN_VARIANCE (Scale 1 - 10)
- **1 - 3 (Conservative / Enterprise)**: Balanced, predictable grid layout. Clean corporate SaaS (Stripe, Linear, GitHub).
- **4 - 7 (Contemporary / High-Craft)**: Asymmetric layout elements, overlapping cards, distinctive typographic scales, dynamic accent highlights.
- **8 - 10 (Editorial / Avant-Garde)**: Expressive brutalism, massive typography, fluid organic shapes, boundary-pushing layouts (Awwwards, Studio portfolios).
- *Default recommended for Hermes web projects: 6 - 7.*

### Dial 2: MOTION_INTENSITY (Scale 1 - 10)
- **1 - 3 (Snappy / Instant)**: Micro-transitions (<180ms). Hover states and instant feedback. No heavy scroll or entrance delays.
- **4 - 7 (Delight / Fluid)**: Coordinated spring physics, staggered list entrances, interactive card tilts, smooth modal reveals.
- **8 - 10 (Immersive / Cinematic)**: Full page transitions, continuous ambient mesh glows, parallax depth.
- *Default recommended for Hermes web projects: 5 - 6.*

### Dial 3: VISUAL_DENSITY (Scale 1 - 10)
- **1 - 3 (Spacious / Minimalist)**: Generous whitespace, large margins, hero-focused layouts.
- **4 - 7 (Product / Dashboard)**: Balanced information density. Scannable tables, badges, metrics, and KPI cards.
- **8 - 10 (High-Throughput)**: Bloomberg terminal or developer console style. Compact rows, monospace accents.
- *Default recommended for Hermes web projects: 5 - 7.*

---

## 2. Strictly Prohibited "AI Slop" Clichés

Hermes MUST NEVER output the following generic AI tropes:
1. ❌ **The "Purple-Pink AI Gradient"**: No lazy `linear-gradient(to right, #8b5cf6, #ec4899)` on every button, heading, or border.
2. ❌ **The "Three Floating Centered Cards"**: Avoid 3 identical square cards placed side-by-side with centered text and a generic circle icon.
3. ❌ **Default System Fonts**: Never rely on raw `Arial`, `Times`, or basic `sans-serif`. Always specify tailored typography (`Outfit`, `Plus Jakarta Sans`, `Inter`, `JetBrains Mono`).
4. ❌ **Heavy Flat Black Shadows**: No `box-shadow: 0 4px 6px #000`. Use layered, diffuse shadows with color tinting (`0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 0 15px rgba(99, 102, 241, 0.15)`).
5. ❌ **Centered Body Paragraphs**: Never center multi-line explanatory text. Left-align for natural readability.

---

## 3. High-Craft Aesthetic Principles

1. **Curated Color Palettes**:
   - Primary dark canvas: Deep slate/navy (`#080c14` or `#0b0f19`), never flat `#000000` or washed-out gray `#222`.
   - Card surfaces: Glassmorphic translucent layers (`rgba(15, 23, 42, 0.85)`).
   - Dynamic Accents: Singular, deliberate accent tones (e.g. Electric Emerald `#10b981`, Cyber Cyan `#0ea5e9`, or Royal Indigo `#6366f1`).
2. **Surface Depth & Border Lights**:
   - Every card must have a subtle, translucent inner border: `border: 1px solid rgba(255, 255, 255, 0.08)`.
   - Hover states should elevate the border brightness (`rgba(99, 102, 241, 0.45)`) and add subtle glow.
3. **Typographic Rhythm**:
   - Contrast between headline font (bold, tight letter spacing: `-0.02em`) and body font (comfortable line-height: `1.5 - 1.6`).
   - Use monospace typography (`JetBrains Mono`) for IDs, numbers, codes, and timestamps.
