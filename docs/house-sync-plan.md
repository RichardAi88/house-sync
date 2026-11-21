# House Sync project plan

## Goal

Carry a client’s pre-construction change through drafting, estimating, scheduling and purchasing on one model revision. The audience is Australian custom and semi-custom home builders, where nearly every house is tailored to the client and variations repeatedly pass between teams.

The drafter updates the model, drawings and specification. The estimator compares the proposed revision B with the previous revision A and prepares the variation for the client. If the client requests a different design, the drafter revises it and the estimator prices the new proposal. Once the client accepts, the scheduler releases the BOQ for the accepted revision and purchasing uses its quantities to raise orders. Each document identifies the revision it came from.

The project starts from [homespec](https://github.com/maccman/homespec), an MIT-licensed house modelling and export engine. The original licence, attribution, and source history are retained. House Sync adds a local browser application, validated inputs, pricing allowances, and document exporters.

## Current status on `main`

The delivered local workflow includes AUD trade pricing, the CSV rate library and overrides, constrained plan editing, orthogonal partitions, named rooms, room finish schedules, grouped BOQs, draft purchasing and client variation quotes with separate internal detail. The default is one room in a rectangular shell. Adding partitions creates rooms measured from the same model.

The drafter, estimator, scheduler and purchasing team work from saved revision IDs. The proposed BOQ is calculated with the model update. Client acceptance, BOQ release and supplier issue remain manual builder decisions. Approval and issue registers are planned.

## Delivered scope

The demonstration starts with a single room in a rectangular shell, as a concept house. Default dimensions are **10,000 x 8,000 mm clear internally**, with a 3,000 mm wall height and 200 mm walls. Its external wall footprint is 10,400 x 8,400 mm.

| Element | Current scope |
| --- | --- |
| Walls | Four two-leaf exterior brick walls and optional orthogonal internal partitions, with room finish selections |
| Floor | 150 mm slab within the clear internal room footprint |
| Roof | 200 mm flat roof with 300 mm overhang beyond the external wall faces |
| Doors | Editable hosted openings; default D1 is centred on the south wall, 1,000 x 2,100 mm |
| Windows | Editable hosted openings; defaults N1/N2 are centred north/east, 1,500 x 1,200 mm with 900 mm sill |
| Editable settings | House dimensions, external facebrick or rendered paint, inner brick bare or plastered and painted, paint coats, per-item wastage, currency, rate overrides and source/date; stable-ID doors/windows with host, start, width, height and sill |
| Plan and browser model | Direct constrained plan edits, snapping, undo/redo and draft review; compiled 3D view |
| BOQ | Shell and room lines using eleven trade codes, exported to Excel and CSV, with net quantities, wastage, gross priced quantities and row-level rate provenance |
| Rate library | Editable `rates/rates_aud.csv`, one source/date per BOQ code, library snapshot SHA-256, optional project overrides |
| Technical specification | Draft schedule of dimensions, materials, finishes, and measurement scope, exported to Word and PDF |
| Drawings | Dimensioned floor plan and south/east elevations, exported to SVG and PDF |
| Client variation quote | Proposed revision's markup, fee, credit policy, PC/PS movements, signed GST and validity; separate client/internal PDFs |
| Purchasing | Fillable draft PDF per saved BOQ row or complete ZIP, without repricing a historical revision |
| Model data | IFC, compiled intermediate representation, source settings, and upstream schedules/drawings |

All exported documents are snapshots of the selected design revision. Editing an exported spreadsheet or Word document does not change the model. Saved settings provide the application's editable inputs.

## Implemented core

- Build the demonstration through homespec's geometry compiler and extract its final model quantities.
- Deduct the modelled openings, count parent door/window products once, and resolve each effective rate from a validated library row or an explicit project override.
- Use stable element IDs across model data, BOQ references, and drawings.
- Generate all three document families from the same compiled result.
- Include a revision based on settings, application source files and the recorded rate-library basis.
- Keep local generated revisions in the ignored output directory while code, tests, plans, and decisions are tracked in Git.

- Preserve the proposed revision's commercial settings and allowance balances separately from measured BOQ quantities.
- Prepare separate client and internal quote documents from saved A/B snapshots; retain the issue date on reopening.
- Keep supplier ordering and approval decisions with the builder.

The local server and browser interface integrate these components. The README contains the current startup and verification instructions.

## Measurement and pricing basis

| Quantity | Rule |
| --- | --- |
| Floor slab, floor finish and ceiling area | Slab covers the original clear shell. Floor finishes and ceiling allowances cover the clear derived room regions |
| Wall volume | Final compiled wall volume after opening cuts; corner returns counted once |
| Equivalent net wall area | Wall volume divided by wall thickness, retained for the uniform rectangular shell |
| Skirting | Compiled internal room perimeter minus the widths of compiled door openings |
| Internal plaster/render | Net selected shell inner faces and exposed partition faces, deducting compiled opening voids |
| External render | Net outer faces for the rendered option, deducting compiled opening voids |
| Measured paint area | Net vertical wall faces selected for paint; excludes reveals, sills, soffits and floor/roof/ceiling painting |
| Roof area | Compiled flat roof top surface, including the 300 mm overhang |
| Door/window counts | Parent products only; emitted frame, leaf and glazing parts do not add products |

The paint quantity applies an editable coat multiplier, default 2, to the measured net paint area. Each BOQ item records net quantity, wastage percentage and gross quantity; pricing uses the gross quantity. Default wastage is 10% for floor finish/skirting, 5% for ceiling/internal plaster/external render, and 0% for the existing items, including paint. Coat count does not alter the measured wall area or geometry.

New designs default to AUD. `rates/rates_aud.csv` supplies one rate for each of the eleven BOQ codes, with independent door and window rows. The columns are `code`, `description`, `unit`, `rate`, `currency`, `source`, `effective_date` and `notes`. The shipped rows are **illustrative AUD rates, not a tender estimate**, dated 2026-11-17; they do not represent supplier quotes. Each modern design may override selected codes, retaining override source/date separately. Blank overrides follow the library; explicit zero is a zero-cost override. Taxes, preliminaries, foundations, services and unmodelled work remain excluded.

A new 10 × 8 m design uses exposed facebrick outside and plaster and paint inside. It has 107.100 m² of outer facebrick, 102.300 m² to paint and an illustrative BOQ subtotal of AUD 46,904.22. Bare common brick inside brings the subtotal to AUD 42,055.20. Partitions retain their scheduled finishes. The 12 × 8 m facebrick design with plaster and paint inside costs AUD 54,494.22 at the same rates.

For the rendered reference design, check quantities are 80.000 m2 clear floor/finish/ceiling area, 20.940 m3 net wall volume, 35.000 m skirting, 102.300 m2 internal plaster/render, 107.100 m2 external render, 209.400 m2 measured paint area (418.800 m2 at two coats), 99.000 m2 roof area, one door and two windows. At default wastage and rates, the subtotal is **AUD 53,330.22**. Increasing the length to 12 m gives **AUD 61,640.22**, a difference of **AUD 8,310.00**; the README contains the itemised worked example.

Older designs retain explicitly saved rates as overrides and retain their currency. Pre-finishes designs may lack new item prices: when the current library has another currency, supply all missing rates or select a matching-currency library before building. Saved revision BOQs are never repriced or rewritten; comparisons still use their recorded amounts and reject different currencies. Coat or wastage edits affect the quantity component. With a known library basis in both revisions, rate effects are attributed first to library changes holding old overrides, then to override changes at the new library and quantity. Historical missing basis remains unknown.

Edit the CSV, use **Refresh rate library** on the **Rates** tab, review values and per-row sources/dates, then explicitly update the house and documents. Optional per-item overrides and **Use library** controls manage project exceptions. A custom file can be selected with `--rates`. Generated revisions preserve the full SHA-256 of the raw library bytes and a detached copy of its rows, plus effective rate provenance for the coordinated exports; merely changing the CSV does not rewrite saved revisions.

### Client quote pricing

The proposed revision B supplies one combined overhead and margin markup, default 20% on cost, a fixed administration fee, default zero, a credit policy and a validity period, default 14 calendar days. PC and PS balances are separate from the measured BOQ and only their A-to-B value movements are priced. Apply markup to aggregate additions; credits are at cost by default or include markup when selected. The fixed fee applies once to any priced movement, including credit-only and offsetting variations, and is never marked up. Identical, editorial and equal-value PC/PS transfers attract no fee. Round each aggregate markup half-up to cents, then signed GST at 10% on the final ex-GST subtotal.

The original 10 m to 12 m example has an AUD 8,310.00 cost increase and an AUD 10,969.20 client variation including GST with default settings. Adding the worked study partition and door changes the internal cost by AUD 3,708.12. Additions of AUD 3,803.12 and credits of AUD 95.00 give an AUD 4,915.61 client variation including GST with credits at cost. The client PDF shows the commercial totals, scope and allowance movements. The internal PDF also records trade detail and rate sources. The first quote preparation fixes its issue date in UTC, and reopening preserves the saved dates and files. See the README for the full worked quote, credit cases and additional allowance example.

## Verification of the builder workflow

The README provides reproducible baseline, extension and partition takeoffs, with client quote examples using default commercial settings. Integration tests publish a room design, generate its drawings, specification, BOQ and IFC, compare two saved revisions, inspect the client and internal PDFs, and download purchase orders from the same saved BOQ. Historical outputs must remain unchanged after later model, rate, pricing or date changes.

The combined application passed **448 House Sync Python tests and 59 JavaScript tests on Windows**, with repository-wide Ruff and syntax checks for every browser JavaScript file passing. Branch-specific setup evidence below is retained as history and is not added to those counts.

Integrated generation reproduced the default AUD 53,330.22 BOQ, the two-room AUD 57,038.34 BOQ and the three-room AUD 59,872.90 BOQ. The fixed-P1 two-room extension produced AUD 65,348.34 and an AUD 10,969.20 client variation including GST. The two-room to three-room variation was AUD 3,757.16 including GST. The complete revision purchase-order sets contained 11, 18 and 24 PDFs respectively, with saved amounts reconciling to their BOQs. Client and internal quote files were checked separately.

The four-role review checks whether the drafter can explain the model revision, the estimator can reconcile the client price, the scheduler can identify the accepted BOQ, and purchasing can trace every draft order to that revision. Software validation does not supply structural, regulatory or contractual approval.

## Verification completed during setup

Engine tests check input validation, geometry and quantity changes, opening deductions, stable revision behaviour, and paint changes without geometry changes. Exporter tests check shared revision/content, spreadsheet formulas, current opening dimensions, SVG escaping, and spreadsheet text handling.

Actual default exports were generated locally. The drawing PDF was rendered and visually inspected as one A3 page; the specification PDF was rendered and visually inspected as one A4 page. The Excel workbook was imported, recalculated, and visually reviewed with the bundled spreadsheet engine; its total matched the model calculation.

The DOCX package and contents passed structural tests. Visual DOCX rendering was unavailable on the setup host: LibreOffice was absent and native Word automation could not obtain a Windows logon session. Word layout should be visually checked on a working Word/LibreOffice installation before treating it as a reviewed issue document.

## Next features in builder workflow order

| Team | Available on `main` | Next development priority |
|---|---|---|
| 1. Drafter | Constrained shell editing, orthogonal partitions, stable named rooms, room finish schedules, dimensioned plans, IFC and 3D views | Extend beyond orthogonal partitions and rectangular shells. Add per-face finishes and a product palette with physical colour references, substrate, preparation and coating systems. |
| 2. Estimator | Illustrative AUD trade rates, dated CSV library, project overrides, A-to-B quantity/rate effects, client quotes, fixed fee, credit policy and separate PC/PS movements | Add verified supplier quotations, location context and project-specific tax treatment beyond the fixed quote GST rule. Record the client's decision against the specific priced revision. |
| 3. Scheduler | Compiled shell and room BOQ groups, finish trades, paint coats, wastage and immutable revision exports | Record which BOQ is released for an accepted variation, with an issue register and controlled approval state. |
| 4. Purchasing | One fillable draft purchase order per saved BOQ row and a ZIP of the complete set | Link supplier/product selections, material coverage and pack sizes to order lines; record issued orders and delivery status. |

Configurable document templates and an authenticated hosted service remain longer-term options after the local workflow is established. Client sign-off and supplier ordering are manual builder decisions; the current exporters do not send orders or update approval state.

## Engineering and contractual limits

The demonstration has no verified structural design, foundations, reinforcement, waterproofing design, thermal performance, fire strategy, services design, or regulatory submission. Geometry consistency and passing software tests do not establish engineering compliance. Homespec's building guideline, IDS, and regulatory checks have not been run for this demonstration.

The technical specification is a draft attachment that records selected model data. It does not establish complete contractual scope, commercial terms, project approvals, or construction readiness. Detailed products, performance requirements, construction details, and project-specific measurement rules remain to be defined.
