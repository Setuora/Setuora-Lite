# Setuora interface design

Master and Lite implement the supplied warm editorial design reference using the same visual system. Their common assets are deliberately copied into each independently deployed app, so neither installation needs the other repository at runtime.

## Shared assets

Keep `app/static/design-system.css`, `navigation.js`, `table-viewport.js`, and `fonts/` identical between Master and Lite. Load `styles.css` first for feature-specific layouts, followed by `design-system.css` for shared tokens and components. Update the asset version in `base.html`, `login.html`, and `account_password.html` when changing shared styles.

- Cream canvas: `--canvas` (`#faf9f5`); cream cards: `--surface-card` (`#efe9de`).
- Primary actions: `--primary` (`#cc785c`), pressed state `--primary-active` (`#a9583e`).
- Dark product surfaces: `--surface-dark` (`#181715`) for payloads, XML and account menus.
- Cormorant Garamond 500 provides the reference's open-source display substitute, with negative tracking. Inter handles body text, navigation, and data; JetBrains Mono handles code and references. All three are served locally; sources and licenses are in `app/static/fonts/`.
- Buttons and inputs use 8px corners; containers use 12px corners. Depth comes from surface color and hairline borders.
- Content is capped at 1200px. Operational sections use 24–32px gaps and padding; the 96px editorial spacing token is reserved for larger breaks rather than making working forms unnecessarily tall.
- Setuora retains its own name and identity.

## Layout and data conventions

Use `.panel`, `.section-title`, `.grid-form`, `.filters`, and `.actions` for common layouts. Grid children must be allowed to shrink with `min-width: 0`. Forms stack on small screens. Place tables inside `.table-scroll` (or `.access-table-scroll` for permissions).

- Put `.numeric` on both a numeric column's header and cells. `.amount` is an equivalent for existing money columns. Values use tabular numerals and align right.
- Use `.cell-wrap` for long names or descriptions, and `.cell-code` for IDs and technical references.
- Put `.col-actions` on action cells and `.table-actions` on an inner container. Do not make a `td` a flex container.
- Keep status text visible; badges can grow for longer states.
- Tables size to actual rows. Long tables scroll after ten records or 70% of viewport height. Empty tables do not receive artificial records. Scrolling regions can be reached by keyboard.
- Live-rendered rows must preserve the same classes as their initial server-rendered equivalents.
- Navigation uses a second row when needed on wide displays, and an expandable menu on small or crowded displays. Native detail menus retain keyboard interaction; Escape closes the current menu and returns focus.
- Print styles expand data tables to expose all records. Lite's physical barcode-label dimensions remain in its feature stylesheet.

## Verification

Run each repository's Python test suite with `.venv/bin/python -m pytest -q`. Check dashboard, populated and empty tables, filters, account screens, forms, menus, and permissions at 1440px, 900px, and 390px. Verify that long values remain contained, table headers and cells align, horizontal scrolling stays inside the table, and live updates retain alignment. Check that font requests stay local and barcode labels retain their configured print size.
