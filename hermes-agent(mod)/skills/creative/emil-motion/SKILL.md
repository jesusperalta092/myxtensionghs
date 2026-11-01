---
name: emil-motion
description: Design engineering & motion principles by Emil Kowalski (animations.dev). Enforces justified motion, spring physics, response times < 300ms, and tactile micro-interactions.
version: 1.5.0
author: Emil Kowalski / Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [motion, animation, spring-physics, micro-interactions, ui, timing, transitions]
    related_skills: [taste-skill, impeccable-design, claude-design]
---

# Emil Kowalski: Motion & Micro-Interactions Framework

Use this skill when implementing animations, transitions, hovers, loaders, modals, drawers, toasts, or state morphing in any web application.

## 1. Core Philosophy: Justified Motion
Motion is not decorative wallpaper; it is cognitive architecture. Every animation must satisfy at least one of these criteria:
1. **Explain spatial continuity**: Where did an element come from, and where does it live when collapsed?
2. **Instant tactile feedback**: Confirm user intent within milliseconds.
3. **Direct attention**: Guide focus to a newly updated status or critical alert.

If an animation does not serve one of these purposes, REMOVE IT.

---

## 2. The Strict Timing Hierarchy

- **Hover & Active States**: `100ms - 150ms` (instant feel, zero perceptual lag).
- **Tooltips & Dropdowns**: `150ms - 220ms`.
- **Modals, Drawers & Overlays**: `240ms - 300ms`.
- **CRITICAL RULE**: NO UI transition should ever exceed `320ms` in a production tool or dashboard. Anything longer feels sluggish.

---

## 3. Mathematical Curves & Easing

### Entrances (Elements entering the screen):
- **NEVER USE `ease-in`**: Starting slow when a user just clicked something makes the app feel unresponsive.
- **ALWAYS USE `ease-out` or Strong Deceleration**:
  ```css
  /* Recommended standard deceleration curve */
  transition-timing-function: cubic-bezier(0.16, 1, 0.3, 1);
  ```

### Exits (Elements leaving the screen):
- Use quick acceleration curves that collapse cleanly:
  ```css
  transition-timing-function: cubic-bezier(0.7, 0, 0.84, 0);
  transition-duration: 160ms;
  ```

### Spring Physics (Transforms & Scales):
- When animating scale (e.g. `transform: scale(0.96) -> scale(1)` on open):
  - Do not overshoot heavily. Use high damping (`stiffness: 400, damping: 30`).
  - Set `transform-origin` to the point of user trigger (e.g. button coordinates).

---

## 4. High-Craft Micro-Interactions

1. **Card Elevation on Hover**:
   ```css
   .card {
     transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1),
                 box-shadow 0.2s cubic-bezier(0.16, 1, 0.3, 1),
                 border-color 0.2s ease;
   }
   .card:hover {
     transform: translateY(-3px);
     border-color: rgba(99, 102, 241, 0.45);
     box-shadow: 0 12px 28px rgba(0, 0, 0, 0.5), 0 0 18px rgba(99, 102, 241, 0.15);
   }
   ```

2. **Button Press Reaction**:
   ```css
   .btn:active {
     transform: scale(0.97);
     transition-duration: 80ms;
   }
   ```

3. **Status Pulse Indicators**:
   - Dot pulse animations must be subtle: opacity between `0.4` and `1.0`, scaling between `0.85` and `1.0`. Never jarring flashes.
