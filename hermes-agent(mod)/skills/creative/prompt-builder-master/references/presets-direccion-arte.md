# Presets de Dirección de Arte

Cada preset define tokens exactos. Elige UNO por proyecto y justifícalo. NUNCA mezcles dos.

---

## 1. Editorial Luxury
**Cuándo**: moda, joyería, arquitectura, hospitality premium, marcas personales senior.

| Token | Valor |
|---|---|
| **Display font** | `Fraunces`, `Instrument Serif`, `PP Editorial New` |
| **Body font** | `Inter`, `Söhne`, `Satoshi` |
| **Paleta** | `#F7F4EE` (hueso) · `#1A1815` (casi negro) · `#8B6F47` (tierra) · `#C9A961` (oro envejecido, uso < 5%) |
| **Spacing scale** | 4 / 8 / 16 / 24 / 48 / 96 / 160px |
| **Radius** | `0` / `2` / `4` (casi todo cuadrado, luxury no redondea) |
| **Motion curve** | `cubic-bezier(0.16, 1, 0.3, 1)` — 600–900ms lentas |
| **Sombras** | Ninguna, o máximo `0 1px 0 rgba(0,0,0,0.06)` |
| **Contraste** | WCAG AAA, texto secundario nunca < `#4A4742` sobre hueso |

---

## 2. Técnico Minimal (Linear / Vercel / Stripe style)
**Cuándo**: SaaS, devtools, fintech, producto B2B, dashboards.

| Token | Valor |
|---|---|
| **Display font** | `Inter Tight`, `Geist`, `Suisse Intl` |
| **Mono font** | `JetBrains Mono`, `Berkeley Mono` |
| **Paleta** | `#0A0A0A` (fondo) · `#FAFAF7` (texto) · `#525252` (muted) · `#3B82F6` o `#22C55E` (1 solo acento) |
| **Spacing scale** | Grid 8pt estricto: 8 / 16 / 24 / 32 / 48 / 64 / 96px |
| **Radius** | `6` / `8` / `12` / `16` (máximo 16px) |
| **Motion curve** | `cubic-bezier(0.4, 0, 0.2, 1)` — 150–300ms rápidas |
| **Sombras** | Sutiles `0 1px 2px rgba(0,0,0,0.4)` en dark |
| **Detalle clave** | Bordes 1px `rgba(255,255,255,0.08)` sobre dark |

---

## 3. Boutique Orgánico
**Cuándo**: wellness, alimentación artesanal, cosmética natural, marcas de autor, ONGs.

| Token | Valor |
|---|---|
| **Display font** | `Reckless`, `Editorial New`, `GT Alpina` |
| **Body font** | `Söhne`, `Inter`, `Sofia Sans` |
| **Paleta** | `#FBF8F1` · `#3B2F2A` (marrón cálido) · `#7A8B6B` (verde oliva) · `#D4A574` (terracota) |
| **Spacing** | 8 / 20 / 32 / 48 / 80 / 128px (más aire que técnico) |
| **Radius** | `0` en cards grandes, `999px` en chips/botones |
| **Motion curve** | `cubic-bezier(0.34, 1.56, 0.64, 1)` con spring soft |
| **Sombras** | `0 24px 48px -12px rgba(59, 47, 42, 0.12)` |
| **Formas** | SVG orgánicos, blobs suaves, no círculos perfectos |

---

## 4. Wellness Soft
**Cuándo**: apps de salud mental, meditación, fitness ligero, finanzas personales amables.

| Token | Valor |
|---|---|
| **Display font** | `General Sans`, `Cabinet Grotesk`, `Sofia Pro` |
| **Paleta** | `#FAF9F6` · `#2D2A26` · `#B5C4B1` (sage) · `#E8D5C4` (peach) · `#A8B8D0` (azul nube) |
| **Spacing** | 4 / 12 / 20 / 32 / 56 / 88px |
| **Radius** | `16` / `20` / `24` (todo redondeado generoso) |
| **Motion curve** | `cubic-bezier(0.4, 0, 0.2, 1)` — 250–400ms, spring suave |
| **Glassmorphism** | `backdrop-filter: blur(20px)` con `bg: rgba(255,255,255,0.6)` |
| **Iconografía** | SVG custom con stroke 1.5px, extremos rounded |

---

## 5. Cyber-Industrial (SOLO si la marca lo exige explícitamente)
**Cuándo**: gaming, música electrónica, ciberseguridad, eventos tech, NFT/digital art.

| Token | Valor |
|---|---|
| **Display font** | `Monument Extended`, `Migra`, `Neue Machina` |
| **Mono font** | `Berkeley Mono`, `iA Writer Mono` |
| **Paleta** | `#0A0E0D` (casi negro) · `#E8FF6B` o `#FF3D71` (1 acento ácido) · `#2A2E2C` (gris carbón) |
| **Spacing** | 4 / 8 / 16 / 32 / 64 / 128px |
| **Radius** | `0` / `2` (casi nulo, industrial) |
| **Motion curve** | `cubic-bezier(0.85, 0, 0.15, 1)` — 200–400ms, cortantes |
| **Detalles** | Scan lines sutiles, glitch < 5% del área, NUNCA todo el UI |
| **Advertencia** | Este preset se ve mal 90% de las veces. Si dudas, no lo uses. |

---

## Cómo elegir

1. Lee el nicho real del proyecto
2. Pregúntate: ¿a quién le habla? (edad, cultura, poder adquisitivo)
3. Elige el preset que MEJOR encaje culturalmente, no el que más te guste
4. Si el proyecto es híbrido (ej. fintech wellness), elige el preset dominante y añade UN acento del otro
5. **Justifica en 2 líneas por qué ese preset y no otro**
