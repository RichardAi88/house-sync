# House Sync design decisions

These records explain the current demonstration model and its linked outputs. Element IDs remain consistent across compiled model data, document references, and the browser application. Inputs and measurement rules are the authority for generated revisions.

## D-001 Use clear internal dimensions for the main house settings

Entities: ROOM1, W1, W2, W3, W4, S0, R0

Length and width describe the clear internal room, defaulting to 10,000 x 8,000 mm. Walls sit outside those reference lines. With default 200 mm walls, the external wall footprint is 10,400 x 8,400 mm. Drawings label clear and external dimensions separately. The model's native units are millimetres, with Z up; browser meshes use metres.

## D-002 Assign corner returns to the south and north walls

Entities: W1, W2, W3, W4

South W1 and north W3 extend one wall thickness beyond the room corners. East W2 and west W4 butt against them. This forms a closed rectangular shell without double-counted corner volumes. Opening positions on the drawing are normalised back to the internal reference line.

## D-003 Keep the first opening arrangement fixed

Entities: D1, N1, N2, W1, W2, W3

D1 is a centred 1,000 x 2,100 mm south door opening. N1 is a centred north window and N2 a centred east window, each 1,500 x 1,200 mm with a 900 mm sill. Settings validation keeps these openings within their hosts. D-016 supersedes the fixed-opening restriction: counts, sizes, hosts and positions are now editable, while these three openings remain the default for older designs. Product selection remains planned work.

## D-004 Model a floor slab within the room footprint

Entities: S0, ROOM1

S0 is 150 mm thick with its top at finished floor level. Its outline follows the clear internal room footprint. It does not represent foundations or a slab supporting the external walls. The BOQ description and elevation graphics preserve this scope.

## D-005 Use one flat roof with explicit overhang

Entities: R0, W1, W2, W3, W4

R0 is 200 mm thick, with its underside at the wall top and 300 mm overhang beyond each external wall face. Roof quantities use the compiled top surface including overhang. The default roof footprint is 11,000 x 9,000 mm, or 99.000 m2. Waterproofing, drainage, reinforcement, and structural adequacy are unverified.

## D-006 Share one paint finish across both wall sides (historical, superseded by D-021)

Entities: W1, W2, W3, W4

The first version has a global paint name and HEX selection, defaulting to Light blue and #ADD8E6. The model, BOQ description, technical schedule, and drawing swatch use the same selection. A screen colour is a visual reference. Manufacturer product, physical colour standard, preparation, and coating system require separate selections in a later product palette.

## D-007 Measure final geometry and state exclusions

Entities: W1, W2, W3, W4, S0, R0, D1, N1, N2, ROOM1

**Historical basis; D-017 supersedes the paint exclusions and extends the trade measurements.** Wall volume comes from the final compiled solids after opening cuts. For this uniform rectangular shell, equivalent net wall area is volume divided by thickness and the combined internal/external net face area is twice that value. Paint excludes reveals, sills, soffits, floor/roof paint, waste, and coat multipliers. Parent door/window products are counted once; their generated parts are not separate openings. These rules must be revisited before supporting nonuniform walls or more complex geometry.

## D-008 Keep prices explicit and illustrative

Entities: W1, W2, W3, W4, S0, R0, D1, N1, N2

**Historical basis; D-017 supersedes the item count, default currency and pricing settings.** The six BOQ items use five editable rates: floor per m2, wall per m3, roof per m2, paint per m2, and one shared allowance per door/window. The currency defaults to SGD. Rates do not represent quotations. The subtotal excludes taxes, preliminaries, foundations, services, and unmodelled work. Separate door/window products and project rate sources are planned.

## D-009 Generate all outputs from one compiled result

Entities: W1, W2, W3, W4, S0, R0, D1, N1, N2, ROOM1

The backend accepts validated settings and invokes homespec. The viewer, quantities, BOQ, specification, and drawings consume the resulting data. A shared revision depends on settings and application source files. The local application publishes completed revisions as output snapshots; exported spreadsheets and documents do not write changes back to the model.

## D-010 Treat the specification as a draft technical attachment

Entities: W1, W2, W3, W4, S0, R0, D1, N1, N2, ROOM1

The generated Word/PDF schedule records dimensions, materials, finishes, linked revision, measurement basis, and scope boundaries. It does not assert engineering approval, complete contractual scope, or sufficient commercial terms. Approved product requirements, technical details, legal terms, and project-specific authority requirements must be established separately.

## D-011 Preserve upstream provenance

Entities: W1, W2, W3, W4, S0, R0, D1, N1, N2, ROOM1

House Sync builds on the MIT-licensed [homespec](https://github.com/maccman/homespec) repository. The original copyright/licence notices and source history are retained. Application-specific code is organised under `housesync`, with project plans, decisions, and tests tracked alongside it.

## D-012 Record verification limits alongside test results

Entities: W1, W2, W3, W4, S0, R0, D1, N1, N2, ROOM1

Automated tests cover model calculations, validation, changes, and export content. Default drawing/specification PDFs were rendered and visually inspected, and the Excel export was recalculated and visually reviewed. DOCX structural/content tests passed, but visual rendering was blocked by the setup host's unavailable LibreOffice/native Word automation. Structural adequacy, materials performance, building-code compliance, and contract sufficiency remain unverified.

## D-013 Preserve A-to-B descriptions and estimate differences

Entities: W1, W2, W3, W4, S0, R0, D1, N1, N2, ROOM1

Each completed design transition compares the previous saved snapshot A with the new snapshot B. Reports preserve source revision IDs, settings changes, before/after quantities and rates, and signed BOQ item differences. Colour or description changes remain visible even at zero cost. The net difference equals B's subtotal minus A's subtotal; additions and omissions reconcile to that amount. Quantity and rate effects use an explicit two-step calculation, with a rounding reconciliation where needed. Unit changes are not assigned a misleading quantity/rate decomposition. Different currencies are rejected instead of assuming an exchange rate.

Comparison reports are immutable, versioned by the two revisions and report source fingerprint, and retained locally. They are estimate changes for the modelled scope, not actual construction change orders: demolition, rework, abortive cost, fees, tax, time and contractual entitlement are not inferred. Failed report export is surfaced without claiming that a completed house build failed or rewriting earlier design snapshots.


## D-014 Export a draft variation order from an explicit comparison

The Changes & cost PDF action produces a separate variation-order.pdf using the selected saved revisions A and B and their reconciled BOQ differences. The document retains change descriptions even at zero cost, identifies its source revisions and a stable comparison reference, and provides contract-reference, review and approval fields. The existing comparison report remains available. Exporting a PDF does not approve or sign the order, infer contractual entitlement, or value unmodelled costs and programme impacts.


## D-015 Make the constrained plan the primary editing workspace

Entities: ROOM1, W1, W2, W3, W4, S0, R0

The plan edits the same millimetre settings used by the compiler. It supports exact dimensions, north/east resize grips, hosted openings, snapping, pan/zoom, measurement, complete-draft undo/redo and review before one explicit update. The south-west datum and rectangular wall topology remain fixed. Draft geometry checks block invalid publication and identify fit/overlap issues; they do not establish regulatory or engineering compliance. The saved baseline is kept separate from the draft so previous issued documents and the resulting variation remain traceable.

A free-form wall drawing tool was deferred because arbitrary wall intersections, room topology and measurement rules need a coordinated model expansion. Validation covers directional host references, undo/redo and invalid drafts, and compiled geometry tests compare the edited model with independent opening deductions. Complex wall topology and building-code compliance remain unverified.

## D-016 Keep editable openings on their compiled hosts

Entities: W1, W2, W3, W4, ROOM1

Doors/windows retain stable D/N identifiers and explicit hosts. Opening starts use the inner reference direction: W1 eastwards, W2 northwards, W3 westwards and W4 southwards. A null start means automatic centring; fixed starts remain fixed when a wall grows. The compiler receives the host-relative start with the corner return allowance on W1/W3. At least 100 mm is required at each inner end, above the opening and between openings on one host. These are concept editing constraints. The application rejects invalid fits instead of silently relocating or shrinking the opening.

The BOQ, specification and plan/elevation schedules use the compiled opening dimensions and IDs. Per-ID change descriptions record additions, removals, moves and size/sill edits, including zero-cost changes. Omitted opening arrays in old saved designs resolve to the original three defaults. Access, structure, door approach zones, circulation, furniture and regulatory suitability require separate project review.

## D-017 Extend geometry take-off to finishing trades and AUD pricing

Entities: ROOM1, W1, W2, W3, W4, S0, R0, and all compiled door/window products.

This decision supersedes the limited trade/paint measurement scope in D-007 and the six-item SGD defaults in D-008. The existing clear dimensions, corner ownership, stable element IDs and compiled geometry authority remain unchanged.

Floor finish and ceiling use compiled clear internal room area. Skirting uses compiled internal perimeter less compiled door opening widths. Internal plaster/render and external render are measured from exact compiled STEP wall faces clipped to the internal room and external shell boundary planes, after opening deductions. This excludes buried corner tabs while retaining exposed corner returns; rounded viewer OBJ coordinates are unsuitable for take-off. Combined internal/external net wall faces remain the measured paint area. Production take-off reads compiled geometry rather than hard-coded sample quantities or a separate browser calculation. Ceiling and finishes are quantity allowances against the shell geometry; this decision does not introduce ceiling framing, finish-layer assemblies or construction system design.

The default 10 x 8 m shell measures 80 m2 floor finish/ceiling, 35 m skirting, 102.3 m2 internal plaster/render and 107.1 m2 external render. The measured paint area is 209.4 m2. The editable coat count defaults to 2, giving a net painted quantity of 418.8 m2; descriptions retain the underlying measured net area. Reveals, sills, soffits, and floor/roof/ceiling paint remain excluded.

Each of the eleven BOQ rows exposes net quantity, wastage percentage and gross quantity. Painting applies coats before wastage. Gross quantity is net quantity multiplied by `1 + wastage / 100`, rounded half-up to six decimal places, and the row amount is gross quantity multiplied by rate, rounded half-up to two decimal places. Default wastage is 10% floor finish/skirting, 5% ceiling/internal plaster/external render and 0% elsewhere, including paint. Changes to coats or wastage alter priced quantities without modifying the geometry.

**Historical pricing defaults; D-018 supersedes the shared opening rate and global library provenance.** New designs default to AUD, with ten editable rates and a shared door/window allowance. The source label is **illustrative AUD rates, not a tender estimate**; no supplier quotes are invented. Validated editable rate source and date fields retain the pricing basis, with 2026-11-17 as the demonstration date. At default rates, coats and wastage, the 10 x 8 m subtotal is AUD 53,330.22 and the 12 x 8 m subtotal AUD 61,640.22. Taxes, preliminaries, foundations, services and all other unmodelled scope remain excluded.

Omitted new settings in old design inputs receive defaults. Comparison normalises those omitted settings before identifying changes, while retaining saved BOQ rows and totals as the authority. Previously issued snapshots are not silently repriced, recast into AUD, or rewritten. Quantity effects use gross priced quantities at the original rate; rate effects use the new gross quantity. Rounding still reconciles to the recorded item amounts. Comparing revisions in different currencies remains refused without an explicit conversion policy.

## D-018 Version the CSV rate library and separate project overrides

Entities: all eleven BOQ codes, their effective rate records and each generated revision.

`rates/rates_aud.csv` is the editable default pricing source. It contains exactly one row per code, including independent `DOOR` and `WINDOW` rows. Its columns are `code`, `description`, `unit`, `rate`, `currency`, `source`, `effective_date` and `notes`. Rows must use the expected measurement units, finite positive library rates, one currency and valid ISO dates. Invalid rows fail explicitly with a CSV line reference. An installed package includes the default CSV; the server can select another complete library with `--rates`.

The shipped AUD allowances remain illustrative, dated 2026-11-17, with no supplier quotes or market-price claim. Editing the file does not make its rates verified. Sources, effective dates, notes and product scope must describe the evidence actually supplied by the user. The source library and project overrides are pricing data; geometry and quantities remain controlled by the compiled model.

Modern settings store optional `rate_overrides` by BOQ code. An explicit override takes precedence over its library row; blank means follow the library and zero is a valid override. Each override uses the project's override source/date, while a library-derived rate uses that row's source/date. **Use library** removes the selected override. A priced build requires matching design/library currency or explicit prices for every item; currency conversion is never inferred.

The **Rates** tab displays the current CSV values and metadata, the current SHA-256 and the displayed revision's saved library SHA-256. **Refresh rate library** reads the file again for review. Applying a different library or override requires an explicit house/document update. Earlier snapshots remain unchanged. Every new revision captures the full SHA-256 over raw CSV bytes, the detached library rows and the resolved per-item rate basis. Changes to source/date or other file bytes can create a new revision without a monetary change.

A-to-B costing first changes quantities at the old effective rate. For revisions with a known library basis, it then changes the library while holding the old overrides and uses the new quantity, followed by changing overrides against the new library at that same quantity. Those effects plus rounding reconcile to the saved BOQ difference. An existing override may therefore shield an item from a library rate change. Missing historical library or override evidence remains unknown; it is not fabricated to force an attribution.

Explicit legacy stored prices are retained as overrides, including the former shared door/window value mapped to both codes. They are not silently reset to the latest CSV. Pre-finishes foreign-currency designs may be opened but must supply missing item prices or a matching-currency library before a new priced build. Original saved BOQs, currencies, amounts and revision identities remain authoritative for historical reports, and cross-currency comparison remains refused.

## D-019 Export draft purchasing forms from the saved BOQ

Entities: each saved BOQ row and its source house revision.

The **Purchasing** tab offers one fillable draft purchase-order PDF per BOQ item and a ZIP of the item PDFs. Each PDF prints the saved revision and a reference derived from its BOQ code. The description, gross priced quantity, unit, effective rate, currency and amount come from the saved BOQ, preserving the same pricing basis as that revision's coordinated documents. Legacy revisions use their existing rows and saved priced quantities; no model rebuild or current-library repricing is required to describe their recorded scope.

A BOQ quantity is a pricing measure, not an inferred supplier pack quantity. Painting keeps its saved coat-adjusted pricing quantity; it is not converted to litres or tins. Other items are not rounded into packs or order increments without explicit product and coverage data. Existing wastage is already part of the saved gross quantity and is not applied again. Illustrative rates remain illustrative, and the form is a draft for review.

Buyer, supplier, required-by, delivery and approval fields may be filled in a compatible PDF viewer after download. The application does not send orders, contact suppliers, sign or approve forms, or write those downloaded form edits back into the house model. A successful house update supplies the revision used for current purchasing downloads; pending model edits do not issue a purchasing set for an unsaved design.

## D-020 Derive rooms from partition centrelines and measure the compiled solids

Partition coordinates are millimetres from the south-west inside corner. Each stable partition ID has a horizontal or vertical centreline, thickness, height and wall type. Its two endpoints meet the inside of the shell or another partition centreline. The line and its authored direction define opening offsets. Wall thickness extends equally on both sides. Flush joins, T-junctions, crossings and closed internal loops are supported. Solid junction ownership is deterministic, and overlapping material at the same elevation belongs to a single wall.

Room polygons come from enclosed regions inside the shell, offset to the modelled wall faces; finishes have no separate thickness. Interior rings subtract from the surrounding region. Door gaps connect adjacent rooms while retaining the wall line as the room boundary. The optional room settings contain names and floor, ceiling, skirting and paint selections. Region identities use a canonical sequence of boundary wall IDs and sides. Moving the same boundaries preserves the room identity and selections. A split or merge may produce a new identity. A design without partitions retains `ROOM1`, its saved BOQ codes and its original quantities.

The application checks orthogonality, endpoint attachments, collisions, minimum clear room width in both orthogonal directions (default 900 mm), and a path through doors from the exterior into every partitioned room. Openings maintain 100 mm end, head, junction and same-host opening clearance. A small detached enclosure with fully joined corners can be reached through its own door. The unpartitioned legacy design can still be opened with its original opening schedule, including an empty schedule. These checks concern the application model. Statutory access, escape, fire performance and structure require project-specific assessment.

The structural slab spans the original clear shell. Each room uses its compiled clear area for floor finish and ceiling and its perimeter less adjacent door widths for skirting. The two sides of an internal door interrupt two runs of skirting. Shell and partition wall volumes use final opening-cut solids and share the existing cubic-metre wall rate. Plaster and paint measure exposed vertical wall faces on the room side, including both sides of partitions. Openings and hidden junction contacts are deducted. Exposed faces above a lower wall at a junction are measured once and allocated to the nearest room on the exposed side, with room ID resolving a tie. Opening reveals, sills, soffits and horizontal partition tops are outside this allowance. The room scope extends over its clear floor footprint even when its bounding partition stops below the shell ceiling.

Shared construction and exterior finishes appear in the shell group. The five internal trade lines carry a stable room ID and their original rate code. Each group subtotal sums rounded item amounts. Changes use the saved quantities and underlying rate codes, and report moved/added/removed partitions and renamed/refinished rooms by ID. The original single-room BOQ retains its combined internal and exterior paint line to preserve issued quantities and comparisons.

In the 10 × 8 m worked example, the full north–south wall at x = 4000 mm and one internal door yield 79.200 m² of clear rooms, 2.190 m³ of partition wall and 48.800 m skirting. Adding the eastern branch at y = 3000 mm and its door yields 78.605 m², 3.765 m³ and 58.500 m respectively. The README gives each room calculation and the priced subtotals.

## D-021 Separate external facebrick and internal common brick finishes

W1–W4 each remain one opening host. The model and IFC represent an outer brick leaf and an inner common brick leaf within the existing overall thickness. The concept allocation is half to each leaf. Cavity width and wall ties require project-specific detailing. The BOQ measures the entire net masonry solid once. Screen colours identify selected appearances. The installed masonry rate comes from the project rate library or its design override.

New editor designs have exposed facebrick outside and plaster and paint inside. The outer leaf can also receive render and paint. The inner leaf can remain bare for future finishes. The compiler measures inside and outside faces separately after openings and junctions. Leaving the inner leaf bare omits only its plaster and paint. Partition faces remain attributed to their adjoining rooms. Both views preview finish edits, including undo and redo. The browser updates colours in place on the last compiled 3D wall geometry. The explicit house update saves the coordinated model, priced BOQ and documents. A colour edit records the finish selection at the same rate. Earlier designs inherit their saved shared paint on both surfaces and retain their priced scope.
