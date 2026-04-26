# Design System Specification: The Industrial Laboratory

## 1. Overview & Creative North Star
**Creative North Star: "The Clinical Engineer"**

This design system moves away from the "dark mode" aesthetic of traditional industrial software to embrace a **Clean Room** philosophy. It is inspired by high-end laboratory environments and aerospace engineering bays—spaces where precision is paramount, and clarity is non-negotiable. 

To break the "template" look, we utilize **Intentional Asymmetry** and **Tonal Depth**. By avoiding the rigid 1px border grids of standard dashboards, we create a layout that feels curated and editorial. Elements do not sit *next* to each other in boxes; they float on distinct planes of light, utilizing whitespace as a structural component rather than a void. This is "Industrial Precision" through the lens of modern luxury.

---

## 2. Color Theory & Surface Architecture

The palette is anchored in technical purity. We use a "High-Contrast, Low-Noise" approach to ensure data remains the protagonist.

### Surface Hierarchy & Nesting
We strictly follow a **Tonal Layering** principle. Instead of using lines to separate content, we use the following stack:
*   **Base Layer:** `surface` (#f7f9fb) — The foundation of the application.
*   **Sectional Layer:** `surface_container_low` (#f2f4f6) — Used for large structural zones (sidebars, secondary panels).
*   **Interactive/Content Layer:** `surface_container_lowest` (#ffffff) — Used for primary cards, data modules, and input areas to make them "pop" against the background.
*   **Elevated Layer:** `surface_container_high` (#e6e8ea) — Used for flyouts or active state background shifts.

### The "No-Line" Rule
**Explicit Instruction:** 1px solid borders for sectioning are prohibited. Boundaries must be defined solely through background color shifts or subtle tonal transitions. If two areas need separation, change the `surface_container` tier.

### The "Glass & Gradient" Rule
To inject "soul" into the technical aesthetic:
*   **Primary Actions:** Use a subtle vertical gradient from `primary` (#0061a4) to `primary_container` (#2196f3). This provides a tactile, "machined" feel.
*   **Floating Elements:** Modals and tooltips must use **Glassmorphism**. Apply `surface_container_lowest` at 85% opacity with a `backdrop-blur` of 12px.

---

## 3. Typography: The Space Grotesk Scale

We use **Space Grotesk** exclusively. Its monospaced-influenced letterforms provide the "Industrial Precision" required, while our editorial scaling ensures high-end readability.

*   **Display (lg/md/sm):** Used for "Hero" readouts or status summaries. Use `on_primary_fixed` (#001d36) for maximum contrast.
*   **Headline (lg/md/sm):** Reserved for section starts. These should be set with tight letter-spacing (-0.02em) to feel like architectural signage.
*   **Title (lg/md/sm):** Used for card headings. Use `primary` (#0061a4) sparingly here to draw the eye to key data points.
*   **Body (lg/md/sm):** The workhorse. Use `on_surface_variant` (#404752) for long-form text to reduce eye strain against the white backgrounds.
*   **Label (md/sm):** All-caps for "Technical Metadata." Labels should always have a +0.05em letter spacing to ensure legibility at small sizes.

---

## 4. Elevation & Depth

We convey importance through **Ambient Shadowing** and **Tonal Stacking**, never through heavy outlines.

*   **The Layering Principle:** To lift a card, place a `surface_container_lowest` (White) object on a `surface_container` (Light Gray) background. The contrast is the "border."
*   **Ambient Shadows:** For floating menus, use a "Cloud Shadow": 
    *   `box-shadow: 0 12px 32px -4px rgba(25, 28, 30, 0.06);`
    *   The shadow is tinted with `on_surface` (#191c1e) at extremely low opacity to mimic natural light.
*   **The "Ghost Border" Fallback:** If a border is required for accessibility (e.g., in high-density data tables), use `outline_variant` (#bfc7d4) at **20% opacity**. It should be felt, not seen.

---

## 5. Components

### Buttons
*   **Primary:** A gradient-filled container (`primary` to `primary_container`). Radius: `md` (0.375rem). Text: `on_primary` (#ffffff).
*   **Secondary:** Ghost style. No background, `outline` color for text, `surface_container_high` on hover.
*   **Tertiary:** Text-only, using `label-md` styling.

### Input Fields
*   **The "Clean Room" Input:** Background is `surface_container_lowest` (#FFFFFF). No border except for a 2px `primary` underline on focus. Labels sit inside the container in `label-sm` style.

### Cards & Lists
*   **No Dividers:** Lists do not use horizontal rules. Separation is achieved through `1rem` of vertical whitespace or a subtle hover shift to `surface_container_low`.
*   **Header Asymmetry:** Card titles should be left-aligned, but metadata (timestamps, IDs) should be right-aligned in `label-sm` typography to create a balanced, technical layout.

### Data Chips
*   **Technical Chips:** Use `secondary_container` (#dae2fd) with `on_secondary_container` (#5c647a) text. Radius: `sm` (0.125rem) to maintain the industrial sharp-edge feel.

---

## 6. Do’s and Don’ts

### Do:
*   **Use Whitespace as Structure:** Allow elements to breathe. Large margins between sections emphasize the "Clean Room" feel.
*   **Respect the Typography Hierarchy:** Use `label-sm` for technical data and `display-md` for the "Answer."
*   **Leverage Tonal Shifts:** Always check if a background color change can replace a border.

### Don't:
*   **Don't use 100% Black:** Typography should never be #000000. Use `on_background` (#191c1e) to keep the "Navy Blue" crispness.
*   **Don't use Rounded Corners on Everything:** While `md` (0.375rem) is the default, keep smaller elements like chips at `sm` (0.125rem) to maintain an "Engineered" look.
*   **Don't add drop shadows to nested cards:** Only the top-most "floating" layer (modals/tooltips) gets a shadow. Everything else is flat tonal stacking.