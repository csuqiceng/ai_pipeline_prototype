# Design System Specification: Industrial Precision

## 1. Overview & Creative North Star: "The Kinetic Monolith"
This design system is built for the high-stakes environment of industrial robotics, where split-second decisions meet heavy-metal execution. Our Creative North Star is **"The Kinetic Monolith."**

Unlike consumer apps that feel light and airy, this system feels grounded, authoritative, and structurally sound. We break the "template" look by moving away from standard card-and-grid layouts in favor of an **asymmetrical command deck**. By utilizing tonal depth and "ghost" boundaries, we create a UI that feels less like a website and more like a high-end physical control console integrated into the machine itself.

**Key Deviations from Standard UI:**
- **Intentional Asymmetry:** The 1380x860 layout should use weighted columns (e.g., a slim, dense telemetry sidebar against a wide, spacious viewport) to create a clear cognitive hierarchy.
- **Tonal Sectioning:** We eliminate "boxes within boxes" in favor of seamless transitions.
- **Monospace Integration:** Monospace is not just for code; it is used as a high-contrast data visualization tool to represent the robot's "truth"—its coordinates and logic.

---

## 2. Colors: Tonal Depth & The "No-Line" Rule
The palette is rooted in deep, atmospheric slates to minimize eye strain in factory environments while making status indicators pop with surgical precision.

### The "No-Line" Rule
**Standard 1px solid borders are strictly prohibited for sectioning.** 
Structural separation is achieved through background shifts. A `surface-container-low` section sitting on a `surface` background provides all the definition a professional eye needs.

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers:
- **Base Layer:** `surface` (#101418) - The "desk" everything sits on.
- **Primary Containers:** `surface-container` (#1C2025) - Main dashboard modules.
- **Nested Detail:** `surface-container-high` (#262A2F) - Active data sets or focused controls.
- **Floating Overlays:** `surface-bright` (#36393F) - Context menus or critical alerts.

### The Glass & Gradient Rule
For floating elements (modals or hovering HUDs), use a **Glassmorphism** approach: 
- **Fill:** `surface-container` at 70% opacity.
- **Effect:** 12px Backdrop Blur.
- **Accent:** A 1px "Ghost Border" using `outline-variant` at 15% opacity to catch the "light."

### Signature Textures
Main CTAs and "Emergency Stop" indicators should use a subtle **Linear Gradient**:
- **Industrial Blue CTA:** Transition from `primary` (#9ECAFF) to `primary-container` (#2196F3) at a 135° angle. This adds "soul" and a tactile, backlit feel.

---

## 3. Typography: Authority & Technical Clarity
We utilize a pairing of **Inter** (for human-readable instructions) and **Space Grotesk** (for high-impact, technical display).

| Level | Token | Font | Weight | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **Display** | `display-lg` | Space Grotesk | 700 | Critical machine state (e.g., "ACTIVE") |
| **Headline** | `headline-md` | Space Grotesk | 500 | Module titles, System sections |
| **Title** | `title-sm` | Inter | 600 | Card headers, Setting categories |
| **Body** | `body-md` | Inter | 400 | General telemetry, logs, descriptions |
| **Label** | `label-sm` | Inter (Mono) | 500 | Coordinates (X, Y, Z), Serial numbers |

**Editorial Note:** Use `label-sm` in monospace for all numerical data. This ensures numbers don't "jump" when values update rapidly, maintaining a steady visual anchor for the operator.

---

## 4. Elevation & Depth: Tonal Layering
In an industrial context, shadows should not feel "flowery." We use **Tonal Layering** to convey importance.

- **The Layering Principle:** To lift a card, move it from `surface-container` to `surface-container-highest`. The shift in hex code provides a "natural" lift.
- **Ambient Shadows:** For critical floating HUDs, use an extra-diffused shadow: `offset-y: 20px`, `blur: 40px`, `color: rgba(0, 0, 0, 0.4)`. 
- **The Ghost Border:** For accessibility in low-light environments, use a `0.5px` border with `outline-variant` at 20% opacity. It should be felt, not seen.

---

## 5. Components: Rugged Precision

### High-Visibility Action Buttons
- **Primary:** Gradient fill (`primary` to `primary-container`), 8px (`lg`) corner radius. High-contrast `on-primary` text.
- **Emergency (Danger):** `error_container` fill. On hover, apply a soft "Crimson Glow" (`box-shadow: 0 0 15px #F44336`).

### Glowing Status Indicators
Status is conveyed through "Bulb" components rather than flat icons:
- **Ready:** `secondary` (#78DC77) with a 4px outer glow.
- **Alarm:** `error` (#FFB4AB) with a pulsing 8px glow animation.

### Data Cards
- **Construction:** Use `surface-container-low`. Forbid divider lines.
- **Separation:** Use `16px` or `24px` vertical whitespace to group telemetry data.
- **Interactivity:** On hover, shift background to `surface-container-high` and apply a subtle `primary` tint to the top-left corner (2px width).

### Coordinate Inputs (Monospace Fields)
- **Background:** `surface-container-lowest`.
- **Text:** `primary-fixed` in Monospace.
- **Focus State:** No thick border; instead, use a 1px `primary` ghost border and a soft blue inner-glow.

---

## 6. Do’s and Don’ts

### Do
- **Do** prioritize "Glanceability." An operator should know the system status from 5 feet away.
- **Do** use `tertiary` (Amber) for "Busy" states—it implies caution without the panic of red.
- **Do** use asymmetric layouts. Place critical "Stop/Go" controls on the right (thumb-zone) and telemetry on the left.

### Don't
- **Don't** use pure white (#FFFFFF). It causes "veiling glare" in dark industrial settings. Use `on-surface-variant` (#BFC7D4) for secondary text.
- **Don't** use standard "drop shadows" on every card. It creates visual clutter. Stick to Tonal Layering.
- **Don't** use icons without labels for critical robot movements. Clarity beats aesthetics in safety-critical systems.

---

## 7. Layout Specification (1380x860)
The screen is divided into three functional zones:
1.  **The Utility Spine (Left, 80px):** `surface-container-lowest`. Minimalist navigation icons.
2.  **The Telemetry Deck (Center, 900px):** `surface`. Deep-view of robot pathing, 3D visualization, and live coordinates.
3.  **The Control Sidebar (Right, 400px):** `surface-container-low`. High-visibility buttons, emergency overrides, and active alarm logs. This section uses the "Monolith" feel—a solid, unmoving block of control.