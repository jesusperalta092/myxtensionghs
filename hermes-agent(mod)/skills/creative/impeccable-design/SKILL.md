---
name: impeccable-design
description: Impeccable design system & determinist UI linter rules by Paul Bakaus (@pbakaus). Imposes strict 8-point spatial rhythm, typographic scale harmony, WCAG AAA contrast, and deep multi-layer glassmorphism.
version: 2.1.0
author: Paul Bakaus / Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [design, spacing, typography, impeccable, layout, design-system, accessibility]
    related_skills: [taste-skill, emil-motion, claude-design]
---

# Impeccable Design: Deterministic System & Linter Rules

Use this skill to enforce mathematical cleanliness, layout cohesion, and typographic harmony across all user interfaces created by Hermes.

## 1. The Strict 8-Point Spatial Grid

Every single dimension, margin, padding, and gap MUST adhere to multiples of `4px` or `8px`:
- `4px` (Quarter step): Micro-padding inside badges, pills, and icon margins.
- `8px` (Half step): Gap between tight elements, button inline icons.
- `12px` (Intermediate step): Input padding, small card gap.
- `16px` (Base unit): Standard card padding, standard gap between form rows.
- `24px` (1.5x Base): Section margin, generous card padding.
- `32px` (2x Base): Grid gap, major column division.
- `48px` / `64px`: Layout boundaries, hero banner padding.

**ABSOLUTE RULE**: NEVER use odd numbers or uncalibrated values like `11px`, `13px`, `19px`, `23px` for spacing.

---

## 2. Typographic Scale Harmony

Always use a calibrated typographic scale (Major Third ratio `1.25`):
- `11px` (`0.6875rem`): Micro-badges, card tags, timestamps. Monospace preferred.
- `12px` / `13px` (`0.8125rem`): Secondary metadata, captions, table body text.
- `14px` / `15px` (`0.9375rem`): Default body text, button labels.
- `18px` (`1.125rem`): Card titles, subsection headings.
- `24px` (`1.5rem`): Main section headers, modal titles.
- `32px` (`2.0rem`): Hero metrics, KPI large numbers.
- `44px`+ (`2.75rem`+): Landing hero display titles.

---

## 3. High-Depth Glassmorphism Formula

For authentic, ultra-premium dark glassmorphism:
```css
.glass-panel {
  /* 1. Translucent Deep Base */
  background: rgba(15, 23, 42, 0.85);

  /* 2. Multi-pass Backdrop Filter */
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);

  /* 3. Subtle Light-Rim Border */
  border: 1px solid rgba(255, 255, 255, 0.08);

  /* 4. Layered Deep Shadow */
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.45);
  border-radius: 14px;
}
```

---

## 4. Accessibility & Contrast Guardrails (WCAG AAA)

1. Text over dark backgrounds (`#080c14`):
   - Primary text: Minimum `#f8fafc` (Slate 50) -> Contrast ratio > 16:1 (AAA).
   - Secondary text: Minimum `#94a3b8` (Slate 400) -> Contrast ratio > 7:1 (AAA).
   - Dim captions: Minimum `#64748b` (Slate 500) -> Contrast ratio > 4.5:1 (AA).
2. Never rely on color alone to convey state:
   - Success states must include an icon or checkmark (`✓`) alongside green color.
   - Warning states must include a symbol (`⚠️`) alongside amber color.
   - Error states must include an icon (`✕`) alongside red color.
