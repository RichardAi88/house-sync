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

D1 is a centred 1,000 x 2,100 mm south door opening. N1 is a centred north window and N2 a centred east window, each 1,500 x 1,200 mm with a 900 mm sill. Settings validation keeps these openings within their hosts. Editable counts, dimensions, positions, and product types are planned work.

## D-004 Model a floor slab within the room footprint

Entities: S0, ROOM1

S0 is 150 mm thick with its top at finished floor level. Its outline follows the clear internal room footprint. It does not represent foundations or a slab supporting the external walls. The BOQ description and elevation graphics preserve this scope.

## D-005 Use one flat roof with explicit overhang

Entities: R0, W1, W2, W3, W4

R0 is 200 mm thick, with its underside at the wall top and 300 mm overhang beyond each external wall face. Roof quantities use the compiled top surface including overhang. The default roof footprint is 11,000 x 9,000 mm, or 99.000 m2. Waterproofing, drainage, reinforcement, and structural adequacy are unverified.

## D-006 Share one paint finish across both wall sides

Entities: W1, W2, W3, W4

The first version has a global paint name and HEX selection, defaulting to Light blue and #ADD8E6. The model, BOQ description, technical schedule, and drawing swatch use the same selection. A screen colour is a visual reference. Manufacturer product, physical colour standard, preparation, and coating system require separate selections in a later product palette.

## D-007 Measure final geometry and state exclusions

Entities: W1, W2, W3, W4, S0, R0, D1, N1, N2, ROOM1

Wall volume comes from the final compiled solids after opening cuts. For this uniform rectangular shell, equivalent net wall area is volume divided by thickness and the combined internal/external net face area is twice that value. Paint excludes reveals, sills, soffits, floor/roof paint, waste, and coat multipliers. Parent door/window products are counted once; their generated parts are not separate openings. These rules must be revisited before supporting nonuniform walls or more complex geometry.

## D-008 Keep prices explicit and illustrative

Entities: W1, W2, W3, W4, S0, R0, D1, N1, N2

The six BOQ items use five editable rates: floor per m2, wall per m3, roof per m2, paint per m2, and one shared allowance per door/window. The currency defaults to SGD. Rates do not represent quotations. The subtotal excludes taxes, preliminaries, foundations, services, and unmodelled work. Separate door/window products and project rate sources are planned.

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
