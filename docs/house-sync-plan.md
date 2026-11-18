# House Sync project plan

## Goal

Carry a client’s pre-construction change through drafting, estimating, scheduling and purchasing on one model revision. The audience is Australian custom and semi-custom home builders, where nearly every house is tailored to the client and variations repeatedly pass between teams.

The drafter updates the model, drawings and specification. The estimator compares the proposed revision B with the previous revision A and prepares the variation for the client. If the client requests a different design, the drafter revises it and the estimator prices the new proposal. Once the client accepts, the scheduler releases the BOQ for the accepted revision and purchasing uses its quantities to raise orders. Each document identifies the revision it came from.

The project starts from [homespec](https://github.com/maccman/homespec), an MIT-licensed house modelling and export engine. The original licence, attribution, and source history are retained. House Sync adds a local browser application, validated inputs, pricing allowances, and document exporters.

## Current scope on `main`

The demonstration is a single rectangular room shell, as a concept house. Default dimensions are **10,000 x 8,000 mm clear internally**, with a 3,000 mm wall height and 200 mm walls. Its external wall footprint is 10,400 x 8,400 mm.

| Element | Current scope |
| --- | --- |
| Walls | Four uniform masonry walls, with one shared paint selection |
| Floor | 150 mm slab within the clear internal room footprint |
| Roof | 200 mm flat roof with 300 mm overhang beyond the external wall faces |
| Doors | Editable hosted openings; default D1 is centred on the south wall, 1,000 x 2,100 mm |
| Windows | Editable hosted openings; defaults N1/N2 are centred north/east, 1,500 x 1,200 mm with 900 mm sill |
| Editable settings | House dimensions, finish, rates, plus stable-ID doors/windows with host, start, width, height and sill |
| Plan and browser model | Direct constrained plan edits, snapping, undo/redo and draft review; compiled 3D view |
| BOQ | Six priced items exported to Excel and CSV |
| Technical specification | Draft schedule of dimensions, materials, finishes, and measurement scope, exported to Word and PDF |
| Drawings | Dimensioned floor plan and south/east elevations, exported to SVG and PDF |
| Model data | IFC, compiled intermediate representation, source settings, and upstream schedules/drawings |

All exported documents are snapshots of the selected design revision. Editing an exported spreadsheet or Word document does not change the model. Saved settings provide the application's editable inputs.

The model update generates the BOQ alongside the drawings and specification. The **Changes & cost** tab compares two saved revisions and exports a draft variation order that identifies both. Client sign-off and release of orders sit with the builder. The purchasing export and AUD pricing are in review.

## Implemented core

- Build the demonstration through homespec's geometry compiler and extract its final model quantities.
- Deduct the modelled openings, count parent door/window products once, and apply editable illustrative rates.
- Use stable element IDs across model data, BOQ references, and drawings.
- Generate all three document families from the same compiled result.
- Include a revision based on settings and application source files.
- Keep local generated revisions in the ignored output directory while code, tests, plans, and decisions are tracked in Git.

The local server and browser interface integrate these components. The README contains the current startup and verification instructions.

## Measurement and pricing basis

| Quantity | Rule |
| --- | --- |
| Floor area | Clear internal room area; the demonstration slab uses the same footprint |
| Wall volume | Final compiled wall volume after opening cuts; corner returns counted once |
| Equivalent net wall area | Wall volume divided by wall thickness, valid for this uniform rectangular shell |
| Paint area | Both internal and external net vertical wall faces, calculated as twice the equivalent net wall area |
| Roof area | Compiled flat roof top surface, including the 300 mm overhang |
| Door/window counts | Parent products only; emitted frame, leaf, and glazing parts do not add products |

Paint quantities exclude reveals, sills, soffits, floor/roof paint, waste, and coat multipliers. The five rate inputs cover floor area, wall volume, roof area, paint area, and a shared allowance per door/window. Rates are illustrative, with SGD as the default currency. Taxes, preliminaries, foundations, services, and unmodelled work are excluded from the subtotal.

For the default geometry, check quantities are 80.000 m2 internal floor area, 20.940 m3 net wall volume, 209.400 m2 paint area, 99.000 m2 roof area, one door, and two windows. With the default illustrative rates, the subtotal is SGD 28,574.80.

## Verification completed during setup

Engine tests check input validation, geometry and quantity changes, opening deductions, stable revision behaviour, and paint changes without geometry changes. Exporter tests check shared revision/content, spreadsheet formulas, current opening dimensions, SVG escaping, and spreadsheet text handling.

Actual default exports were generated locally. The drawing PDF was rendered and visually inspected as one A3 page; the specification PDF was rendered and visually inspected as one A4 page. The Excel workbook was imported, recalculated, and visually reviewed with the bundled spreadsheet engine; its total matched the model calculation.

The DOCX package and contents passed structural tests. Visual DOCX rendering was unavailable on the setup host: LibreOffice was absent and native Word automation could not obtain a Windows logon session. Word layout should be visually checked on a working Word/LibreOffice installation before treating it as a reviewed issue document.

## Next features in builder workflow order

| Team | In review | Further work |
| --- | --- | --- |
| 1. Drafter | Orthogonal partitions, named rooms, room finish schedules and dimensioned plans. [PR #4](https://github.com/RichardAi88/house-sync/pull/4) | Extend plan editing beyond orthogonal walls. Record per-face finishes, product and colour references, substrate, preparation and coating system. |
| 2. Estimator | Illustrative AUD rates, a dated CSV rate library and separate variation effects for changes in quantities, library rates and project overrides. [PR #1](https://github.com/RichardAi88/house-sync/pull/1) · [PR #2](https://github.com/RichardAi88/house-sync/pull/2) | Add supplier quotations, location and tax treatment. Record the client’s decision against the priced revision so the scheduler can identify which revision to release. |
| 3. Scheduler | Finish trades, paint coats, wastage allowances and room BOQ subtotals. [PR #1](https://github.com/RichardAi88/house-sync/pull/1) · [PR #4](https://github.com/RichardAi88/house-sync/pull/4) | Identify the BOQ issued for each accepted variation in an issue register. |
| 4. Purchasing | One fillable draft purchase order per saved BOQ item, with a download for the complete set. [PR #3](https://github.com/RichardAi88/house-sync/pull/3) | Link supplier and product selections to order lines and record which orders have been issued. |

Configurable document templates and an authenticated hosted service remain longer-term options once the local workflow is established.

## Engineering and contractual limits

The demonstration has no verified structural design, foundations, reinforcement, waterproofing design, thermal performance, fire strategy, services design, or regulatory submission. Geometry consistency and passing software tests do not establish engineering compliance. Homespec's building guideline, IDS, and regulatory checks have not been run for this demonstration.

The technical specification is a draft attachment that records selected model data. It does not establish complete contractual scope, commercial terms, project approvals, or construction readiness. Detailed products, performance requirements, construction details, and project-specific measurement rules remain to be defined.
