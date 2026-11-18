# House Sync project plan

House Sync connects one editable house model to a bill of quantities, a draft technical specification, and dimensioned drawings. A user changes the house settings and selects **Update house and documents**. The application rebuilds the model and all affected outputs from the same inputs and records a shared revision.

The project starts from [homespec](https://github.com/maccman/homespec), an MIT-licensed house modelling and export engine. The original licence, attribution, and source history are retained. House Sync adds a local browser application, validated inputs, pricing allowances, and document exporters.

## Current first version

The demonstration starts with a single room in a rectangular shell, as a concept house. Default dimensions are **10,000 x 8,000 mm clear internally**, with a 3,000 mm wall height and 200 mm walls. Its external wall footprint is 10,400 x 8,400 mm.

| Element | Current scope |
| --- | --- |
| Walls | Four exterior masonry walls and optional orthogonal internal partitions, with room finish selections |
| Floor | 150 mm slab within the clear internal room footprint |
| Roof | 200 mm flat roof with 300 mm overhang beyond the external wall faces |
| Doors | Editable hosted openings; default D1 is centred on the south wall, 1,000 x 2,100 mm |
| Windows | Editable hosted openings; defaults N1/N2 are centred north/east, 1,500 x 1,200 mm with 900 mm sill |
| Editable settings | House dimensions, finish, paint coats, per-item wastage, currency, rate overrides and source/date; stable-ID doors/windows with host, start, width, height and sill |
| Plan and browser model | Direct constrained plan edits, snapping, undo/redo and draft review; compiled 3D view |
| BOQ | Shell and room lines using eleven trade codes, exported to Excel and CSV, with net quantities, wastage, gross priced quantities and row-level rate provenance |
| Rate library | Editable `rates/rates_aud.csv`, one source/date per BOQ code, library snapshot SHA-256, optional project overrides |
| Technical specification | Draft schedule of dimensions, materials, finishes, and measurement scope, exported to Word and PDF |
| Drawings | Dimensioned floor plan and south/east elevations, exported to SVG and PDF |
| Model data | IFC, compiled intermediate representation, source settings, and upstream schedules/drawings |

All exported documents are snapshots of the selected design revision. Editing an exported spreadsheet or Word document does not change the model. Saved settings provide the application's editable inputs.

## Implemented core

- Build the demonstration through homespec's geometry compiler and extract its final model quantities.
- Deduct the modelled openings, count parent door/window products once, and resolve each effective rate from a validated library row or an explicit project override.
- Use stable element IDs across model data, BOQ references, and drawings.
- Generate all three document families from the same compiled result.
- Include a revision based on settings, application source files and the recorded rate-library basis.
- Keep local generated revisions in the ignored output directory while code, tests, plans, and decisions are tracked in Git.

The local server and browser interface integrate these components. The README contains the current startup and verification instructions.

## Measurement and pricing basis

| Quantity | Rule |
| --- | --- |
| Floor slab, floor finish and ceiling area | Slab covers the original clear shell. Floor finishes and ceiling allowances cover the clear derived room regions |
| Wall volume | Final compiled wall volume after opening cuts; corner returns counted once |
| Equivalent net wall area | Wall volume divided by wall thickness, retained for the uniform rectangular shell |
| Skirting | Compiled internal room perimeter minus the widths of compiled door openings |
| Internal plaster/render | Net internal vertical wall faces, deducting compiled opening voids |
| External render | Net external vertical wall faces, deducting compiled opening voids |
| Measured paint area | Combined internal and external net vertical wall faces; excludes reveals, sills, soffits and floor/roof/ceiling painting |
| Roof area | Compiled flat roof top surface, including the 300 mm overhang |
| Door/window counts | Parent products only; emitted frame, leaf and glazing parts do not add products |

The paint quantity applies an editable coat multiplier, default 2, to the measured net paint area. Each BOQ item records net quantity, wastage percentage and gross quantity; pricing uses the gross quantity. Default wastage is 10% for floor finish/skirting, 5% for ceiling/internal plaster/external render, and 0% for the existing items, including paint. Coat count does not alter the measured wall area or geometry.

New designs default to AUD. `rates/rates_aud.csv` supplies one rate for each of the eleven BOQ codes, with independent door and window rows. The columns are `code`, `description`, `unit`, `rate`, `currency`, `source`, `effective_date` and `notes`. The shipped rows are **illustrative AUD rates, not a tender estimate**, dated 2026-11-17; they do not represent supplier quotes. Each modern design may override selected codes, retaining override source/date separately. Blank overrides follow the library; explicit zero is a zero-cost override. Taxes, preliminaries, foundations, services and unmodelled work remain excluded.

For the default geometry, check quantities are 80.000 m2 clear floor/finish/ceiling area, 20.940 m3 net wall volume, 35.000 m skirting, 102.300 m2 internal plaster/render, 107.100 m2 external render, 209.400 m2 measured paint area (418.800 m2 at two coats), 99.000 m2 roof area, one door and two windows. At default wastage and rates, the subtotal is **AUD 53,330.22**. Increasing the length to 12 m gives **AUD 61,640.22**, a difference of **AUD 8,310.00**; the README contains the itemised worked example.

Older designs retain explicitly saved rates as overrides and retain their currency. Pre-finishes designs may lack new item prices: when the current library has another currency, supply all missing rates or select a matching-currency library before building. Saved revision BOQs are never repriced or rewritten; comparisons still use their recorded amounts and reject different currencies. Coat or wastage edits affect the quantity component. With a known library basis in both revisions, rate effects are attributed first to library changes holding old overrides, then to override changes at the new library and quantity. Historical missing basis remains unknown.

Edit the CSV, use **Refresh rate library** on the **Rates** tab, review values and per-row sources/dates, then explicitly update the house and documents. Optional per-item overrides and **Use library** controls manage project exceptions. A custom file can be selected with `--rates`. Generated revisions preserve the full SHA-256 of the raw library bytes and a detached copy of its rows, plus effective rate provenance for the coordinated exports; merely changing the CSV does not rewrite saved revisions.

## Verification completed during setup

Engine tests check input validation, geometry and quantity changes, opening deductions, stable revision behaviour, and paint changes without geometry changes. Exporter tests check shared revision/content, spreadsheet formulas, current opening dimensions, SVG escaping, and spreadsheet text handling.

Actual default exports were generated locally. The drawing PDF was rendered and visually inspected as one A3 page; the specification PDF was rendered and visually inspected as one A4 page. The Excel workbook was imported, recalculated, and visually reviewed with the bundled spreadsheet engine; its total matched the model calculation.

The DOCX package and contents passed structural tests. Visual DOCX rendering was unavailable on the setup host: LibreOffice was absent and native Word automation could not obtain a Windows logon session. Word layout should be visually checked on a working Word/LibreOffice installation before treating it as a reviewed issue document.

## Planned development

| Milestone | Intended result |
| --- | --- |
| Implemented: editable openings | Add, remove, resize and move stable-ID doors/windows within validated hosts |
| Implemented: multiple rooms | Orthogonal partitions, derived rooms, finish schedules, junction-corrected quantities and ID-based comparisons |
| Per-wall finishes | Independent internal/external finishes tied to particular wall faces |
| Product palette | Manufacturer, product, physical colour reference, substrate, preparation, and coating system |
| Implemented: finishing trades and AUD allowances | Five finishing trades, editable coats/wastage, AUD defaults and rate source/date metadata |
| Implemented: CSV rate library | Editable eleven-item CSV, per-row source/date, SHA-256 snapshots, per-item overrides/reset and rate attribution |
| Project rates | Supplier quotation integration, product selection, location context and taxes remain |
| Implemented: constrained plan editing | Rectangular shell resize, opening edits, snapping, undo/redo, geometry checks and draft review |
| Further CAD editing | Non-orthogonal partitions and shell shapes |
| Document controls | Revision comparisons and draft variation PDFs implemented; configurable templates, approval states and issue registers remain |
| Deployment | Evaluate an authenticated hosted service after the local workflow is reliable |

## Engineering and contractual limits

The demonstration has no verified structural design, foundations, reinforcement, waterproofing design, thermal performance, fire strategy, services design, or regulatory submission. Geometry consistency and passing software tests do not establish engineering compliance. Homespec's building guideline, IDS, and regulatory checks have not been run for this demonstration.

The technical specification is a draft attachment that records selected model data. It does not establish complete contractual scope, commercial terms, project approvals, or construction readiness. Detailed products, performance requirements, construction details, and project-specific measurement rules remain to be defined.
