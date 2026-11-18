# House Sync

A house model with a coordinated bill of quantities, technical specification, and dimensioned drawings. Change dimensions or the paint finish in the local browser editor, then select **Update house & documents** to regenerate the complete set from one shared model revision.

Built on [homespec](https://github.com/maccman/homespec) by Alex MacCaw. The original MIT licence, source, examples and Git history are retained. Read the [original homespec README](HOMESPEC_README.md) for its compiler and Blender workflows.

## First version

- A single-storey, single-room rectangular shell with a flat roof; one door and two windows by default.
- Editable clear internal length/width, wall height/thickness, paint name/colour and coat count, currency, per-item wastage and optional rate overrides. A versioned CSV library supplies one rate and source/date for each of the eleven BOQ items.
- A primary **Plan editor** for selecting walls, dragging north/east resize grips, editing dimensions, and adding, moving, resizing or deleting doors/windows. Grid snapping, pan/zoom, measurement, undo/redo, geometry checks and a draft change review support direct editing.
- An interactive 3D model: orbit, zoom, select elements, and show/hide the roof.
- Eleven BOQ items in Excel/CSV, including floor finish, ceiling, skirting, internal plaster/render and external render. Net quantity, wastage, gross priced quantity and each item's rate basis remain visible. Technical specification in Word/PDF; floor plan and south/east elevations in SVG/PDF; model in IFC.
- Save/reopen a design as JSON. Previous generated revisions remain available locally.
- Every output identifies the same content-based revision. Pending edits disable downloads until regeneration.
- A **Changes & cost** tab compares saved revision A with revision B, records descriptions of the changes, and shows before/after BOQ quantities, rates, amounts, additions, omissions and the net estimate difference. Reports are available in Excel, CSV, PDF and JSON.

## Edit directly from the plan

The **Plan editor** is the main workspace. Plan measurements and opening positions are in millimetres; the sidebar dimensions are in metres.

1. Select a wall or dimension to enter exact sizes, or drag the north/east shell grips. The rectangular shell stays square and its south-west datum stays fixed.
2. Select a door/window to drag it along its host wall or edit its start, size, sill and host in the inspector. Use the door/window tools to add openings and delete a selected opening to remove it.
3. Use the snap grid for precise placement, pan/zoom or fit the plan, and undo/redo draft edits. A centred opening follows the wall centre until you give it a fixed start position.
4. Review the draft changes and resolve the geometry checks. Openings need 100 mm from wall ends, a 100 mm head clearance, and a 100 mm gap from another opening on the same wall. These are application constraints, not a building-code assessment.
5. Select **Update house & documents** to compile the design, update the 3D model and coordinated exports, and record the A-to-B cost change. The **Changes & cost** tab then provides the variation order PDF.

Draft plan edits do not issue documents. Downloads of the current document set remain disabled until a successful update; a failed validation/build preserves the preceding saved revision. The editor currently supports the rectangular exterior shell and hosted doors/windows. Arbitrary wall segments, internal partitions and multiple rooms require a further model expansion.

## Capture a change from A to B

Every successful update preserves the previous design and automatically creates a comparison with the new design. Open **Changes & cost** to review the last transition, or select any two saved revisions and choose **Compare revisions**. Each report identifies its two source revisions and is saved locally alongside the revision history. Reopening the application preserves these records.

Select **Export variation order PDF** to download a draft variation order for the selected A-to-B comparison. It includes a VO reference, source revisions, change descriptions, itemised quantities/rates and costs, additions/omissions, the net variation and revised estimate, plus contract reference and approval/signature fields. Exporting leaves the order as a draft for review and approval; it does not approve a variation or assess programme impact.

The default 10 x 8 m design has an illustrative subtotal of **AUD 53,330.22**. Increasing its length to 12 m at the same rates, two paint coats and default wastage gives **AUD 61,640.22**, an **AUD 8,310.00 increase**. These are **illustrative AUD rates, not a tender estimate**. A paint colour change at the same quantity and rate is recorded with **zero cost difference**; the application does not invent a product price for a selected screen colour.

The comparison separates gross quantity effects (including coat count and wastage changes) from rate effects and reconciles them to rounded BOQ amounts. Where both saved revisions have a recorded library/override basis, the rate effect is also attributed to library changes and override changes. Historical rows without that evidence retain an unknown attribution instead of an invented split. Missing new settings in older designs receive defaults when loaded and compared; saved revision BOQs and amounts remain immutable. Positive and negative item differences are shown as additions and omissions. Different currencies cannot be compared without an explicit exchange-rate policy. This is an estimate comparison of the modelled BOQ scope; demolition, rework, abortive work, fees, taxes and programme impacts are excluded unless added to the model and rates. It is not an approved contractual variation valuation.

## Run on Windows

Prerequisites: Git, [uv](https://docs.astral.sh/uv/), Python 3.12, and Node.js/npm. The initial installation needs internet access. Subsequent editing and export generation use local dependencies, without Blender, an AI API key, or an external SVG converter.

~~~powershell
git clone https://github.com/RichardAi88/house-sync.git
cd house-sync
uv sync --frozen --extra dev --python 3.12 --cache-dir .cache/uv
npm.cmd ci --cache .cache/npm
.venv\Scripts\python.exe -m housesync.server --open
~~~

After setup, double-click **start-house-sync.cmd**. Open **http://127.0.0.1:8766**. If the editor is already running, the launcher opens it. Keep the launcher running while using the editor; Ctrl+C stops it. GitHub stores source and history; this backend runs locally on this PC's loopback interface.

On macOS/Linux use `.venv/bin/python -m housesync.server --open` after the same uv/npm setup. Initial local verification was on Windows with Python 3.12.

## Maintain rates and overrides

The editable default rate library is [`rates/rates_aud.csv`](rates/rates_aud.csv). It has one row for each BOQ code, including independent `DOOR` and `WINDOW` allowances. The checked-in values are **illustrative AUD rates, not a tender estimate** and do not represent supplier quotes or researched market prices. Every seeded row has `source` set to `illustrative`; its notes retain the disclaimer.

| CSV column | Meaning |
|---|---|
| `code` | Stable BOQ code, such as `FLOOR_FINISH`; each of the eleven codes must occur once |
| `description` | Description of the rate allowance |
| `unit` | Item's measurement unit: `m2`, `m3`, `m` or `each`, matching its BOQ code |
| `rate` | Positive unit rate up to 1,000,000. Coats and wastage are applied to the measured quantity. |
| `currency` | One uppercase three-letter currency for the complete library; the default file uses `AUD` |
| `source` | Actual source or an explicit illustrative label |
| `effective_date` | Date of that rate's pricing basis, in `YYYY-MM-DD` format |
| `notes` | Scope, exclusions or other rate qualifications |

1. Edit the CSV in a text editor or spreadsheet application and save it as UTF-8 CSV. Keep all codes and their expected units, and keep one currency throughout the file. Supply genuine source/date information when replacing an illustrative allowance; the application does not obtain quotes for you.
2. Open the editor's **Rates** tab and select **Refresh rate library**. Review the loaded values, sources, dates and notes. The tab identifies the current library SHA-256 and the library saved with the displayed revision.
3. Edit an item's project override directly in the **Rates** panel or in the sidebar's **Rate overrides**. Leave the field blank to follow its library row; an explicit `0` is a zero-cost override. **Use library** clears that item's override from either location. The two controls edit the same draft value. Record the source/date for project overrides in the sidebar.
4. Select **Update house & documents** to generate the coordinated model and documents at the reviewed rates. Refreshing the library or editing an override leaves the draft pending; it does not rewrite earlier snapshots.

For a separate project library, start the server with an explicit file:

~~~powershell
.venv\Scripts\python.exe -m housesync.server --rates C:\path\to\project-rates.csv --open
~~~

The loader rejects invalid rows with a CSV line reference. A build needs either a library matching the design currency or explicit overrides for all eleven items; no exchange rate is assumed. A packaged installation includes the default CSV, so it does not depend on the current working directory.

For example, the default 80 m2 floor finish has 10% wastage, giving **88 m2** gross. At AUD 65/m2 its amount is **AUD 5,720.00**. Changing the library rate to 70 with no override gives **AUD 6,160.00**, a **AUD 440.00 library rate effect**. Keeping the library at 65 and entering a 70 override gives the same amount, attributed to the override. If an override already pins the item at 70, changing the underlying library row does not change that item's effective rate.

Each generated revision saves the library's full SHA-256 over its raw CSV bytes and its row data, plus the effective rates and their provenance. BOQ and specification exports identify whether each effective rate came from the library or an override, with the source/date. A source/date-only CSV edit changes the library fingerprint and may produce a new revision while leaving costs unchanged. Quantity, library and override effects are calculated in that order, with rounding reconciled to the saved item amounts.

Existing designs with explicit stored rates retain those values as overrides rather than silently adopting the current library. Older pre-finishes designs in another currency may not contain prices for every new trade: they can be opened, but missing values or a matching-currency library must be supplied before building. Saved BOQs remain authoritative for historical comparisons, and different currencies still cannot be compared.

## A change you can verify

The default house has **10 x 8 m clear internal dimensions**, 200 mm walls and 3 m wall height. One 1.0 x 2.1 m door and two 1.5 x 1.2 m windows deduct **5.7 m2** from each set of wall faces. These hand calculations check the compiled model quantities:

| Measured quantity | Default 10 x 8 m | Length changed to 12 x 8 m |
|---|---:|---:|
| Clear floor / floor finish / ceiling area | 80.000 m2 | 96.000 m2 |
| Net wall volume | 20.940 m3 | 23.340 m3 |
| Flat roof area, including overhang | 99.000 m2 | 117.000 m2 |
| Skirting: clear internal perimeter less door widths | 35.000 m | 39.000 m |
| Internal plaster/render: internal perimeter x height less openings | 102.300 m2 | 114.300 m2 |
| External render: external perimeter x height less openings | 107.100 m2 | 119.100 m2 |
| Net paint area: internal plus external wall faces | 209.400 m2 | 233.400 m2 |
| Painted quantity at 2 coats, before wastage | 418.800 m2 | 466.800 m2 |

For example, the default internal wall faces are `2 x (10 + 8) x 3 - 5.7 = 102.3 m2`. The external faces use the 10.4 x 8.4 m external footprint: `2 x (10.4 + 8.4) x 3 - 5.7 = 107.1 m2`. Skirting is `2 x (10 + 8) - 1.0 = 35 m`. These equations are independent checks; production quantities come from the compiled model.

| BOQ item | Default rate (AUD/unit) | Default wastage | 10 x 8 m amount (AUD) | 12 x 8 m amount (AUD) | Change (AUD) |
|---|---:|---:|---:|---:|---:|
| Floor slab, m2 | 95.00 | 0% | 7,600.00 | 9,120.00 | +1,520.00 |
| Masonry walls, m3 | 380.00 | 0% | 7,957.20 | 8,869.20 | +912.00 |
| Flat roof, m2 | 145.00 | 0% | 14,355.00 | 16,965.00 | +2,610.00 |
| Wall painting, m2 per coat | 9.00 | 0% | 3,769.20 | 4,201.20 | +432.00 |
| Door allowance, each | 650.00 | 0% | 650.00 | 650.00 | 0.00 |
| Window allowance, each | 650.00 | 0% | 1,300.00 | 1,300.00 | 0.00 |
| Floor finish, m2 | 65.00 | 10% | 5,720.00 | 6,864.00 | +1,144.00 |
| Ceiling, m2 | 45.00 | 5% | 3,780.00 | 4,536.00 | +756.00 |
| Skirting, m | 18.00 | 10% | 693.00 | 772.20 | +79.20 |
| Internal plaster/render, m2 | 28.00 | 5% | 3,007.62 | 3,360.42 | +352.80 |
| External render, m2 | 40.00 | 5% | 4,498.20 | 5,002.20 | +504.00 |
| **Subtotal** | | | **53,330.22** | **61,640.22** | **+8,310.00** |

The default library gives doors and windows the same AUD 650 allowance in separate rows; they can be priced independently. Changing only the paint colour updates the model and finish descriptions while preserving measured quantities. Coat count, each item's wastage and rate overrides can be edited independently. The worked totals use the checked-in library with no overrides.

## Measurement and document scope

Model dimensions are in millimetres; browser geometry is in metres. Drawings distinguish internal clear dimensions from external overall dimensions. Wall corners are counted once. All take-off quantities are derived from compiled geometry, including hosted opening dimensions:

- Floor finish and ceiling use the clear internal room area. Ceiling is an area allowance over that footprint; no ceiling framing or suspended system is modelled.
- Skirting uses the clear internal perimeter minus every modelled door width; windows do not reduce it.
- Internal plaster/render and external render use their respective net vertical wall faces and deduct all modelled door/window opening voids. They are separate trades from the structural masonry volume.
- Painting uses both sets of net wall faces. The description retains the measured net area; the editable coat count defaults to **2**. `Net painted quantity = measured area x coats`. Ceiling, floor, roof, reveals, sills and soffits are excluded from painting.
- Each BOQ item has an editable wastage percentage. `Gross quantity = net quantity x (1 + wastage / 100)` and `Amount = gross quantity x rate`, rounded half-up to cents per row. Paint defaults to 0% wastage, with coats applied separately. Wastage is a pricing allowance, not a change to model dimensions.
- The roof includes a 300 mm overhang. Door/window rows count parent products once, excluding emitted frame, leaf and glazing parts.

New designs default to **AUD**. The library rates are labelled **illustrative AUD rates, not a tender estimate**; they are demonstration inputs, not supplier quotes or market research. Each CSV row supplies its own source and effective date; the demonstration date is **2026-11-17**. Project overrides retain their own source/date. Loading an old design preserves its explicitly stored rates and currency. No currency conversion is inferred, and revisions in different currencies cannot be compared.

The BOQ now includes the slab, masonry, flat roof, opening allowances and five finishing trades with wall painting. It remains a concept estimate: taxes, preliminaries, foundations, reinforcement, drainage, services, waterproofing, demolition/rework, fees and programme effects are excluded. Product systems, preparation, thicknesses for finishing layers, construction details and regulatory performance require project-specific selections. The specification is a draft technical schedule for development alongside a contract. The demonstration does not claim that homespec's building guideline or regulatory checks have been run.

## Files and development

| Path | Purpose |
|---|---|
| `housesync/schema.py` | Validated house parameters, legacy prices and rate overrides |
| `rates/rates_aud.csv` | Editable, versioned default rate library with source/date per BOQ item |
| `housesync/rates.py` | CSV validation, library fingerprint and snapshot data |
| `housesync/engine.py` | Homespec geometry/IR, quantities, BOQ, IFC and browser mesh |
| `housesync/documents.py` | Coordinated Excel, Word, PDF and SVG exports |
| `housesync/changes.py` | Described A-to-B revision comparison and change-cost report exports |
| `housesync/server.py` | Local server and immutable revision publication |
| `housesync/static/` | Browser editor and Three.js viewer |
| `out/house-sync/<revision>/` | Generated files; excluded from Git |
| [Project plan](docs/house-sync-plan.md) | Scope, verification and next features |
| [Decisions](docs/house-sync-decisions.md) | Geometry, measurement and revision decisions |

~~~powershell
.venv\Scripts\pytest.exe tests/test_housesync_engine.py tests/test_housesync_boq.py tests/test_housesync_documents.py tests/test_housesync_server.py tests/test_housesync_changes.py tests/test_housesync_plan.py tests/test_rate_library.py tests/test_housesync_rate_design.py tests/test_housesync_rate_workflow.py tests/test_housesync_rate_provenance.py -q
.venv\Scripts\ruff.exe check .
Get-ChildItem housesync/static -Filter *.js | ForEach-Object { node --check $_.FullName; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
node --test tests/plan-core.test.mjs tests/plan-editor.test.mjs tests/boq-editor.test.mjs
~~~

Windows uses build123d 0.10 to avoid an import failure caused by scanning an invalid Windows font with 0.11. The generation lock includes a Windows msvcrt implementation. Linux/macOS use build123d 0.11, preserving the advanced geometry API and Python 3.13 compatibility used by the upstream test suite.

Verification for the rate library passed **196 House Sync Python tests**, **38 JavaScript tests**, repository-wide Ruff and syntax checks for every browser JavaScript file. A design saved by the preceding AUD version retained its AUD 54,669.22 subtotal and reported zero changes after all library prices were increased by 20%. The variation order PDF was rendered and the Excel BOQ recalculated.

In a live Chrome session, overriding the slab rate from AUD 95 to 105 increased the estimate by AUD 800. Resetting the override restored the original subtotal. Updating the CSV slab rate to AUD 115 produced an AUD 1,600 library effect and a zero override effect.

The wider non-Blender tests have 20 existing Windows failures or errors matching the unchanged baseline by test ID. Seven involve symlink privileges, twelve involve advanced geometry and one involves a retained-camera source hash. Blender integration was not run locally.

The upstream library_room build was verified on this PC with **51 checks passed, zero failed**. Local tests independently compare quantities with rectangular-shell calculations and IFC geometry. Generated PDFs and the recalculated spreadsheet were visually inspected. Word package/content tests pass; native Word/LibreOffice visual rendering was unavailable. Six upstream reliability tests that create symbolic links were blocked by the Windows account's symlink privilege; they are not reported as passes.

## Plan editing audit evidence

The edited-shell audit fixture compiled with zero clashes and 28 passing upstream checks. Its glazing ratio was 5.6% against the upstream project's 10% guideline, which remains an unresolved concept finding. It is not represented as a statutory requirement. The optional Blender dressed-scene audit was attempted but could not run because Blender is unavailable; furniture/circulation and building-code compliance are not certified by the editor's geometry checks.

The original plan editing audit covered plan logic, API/build/export tests and visual checks of the generated drawing and specification PDFs. It did not include a live pointer interaction review.

## Licence

MIT. See [LICENSE](LICENSE). Dependencies retain their respective licences. This project acknowledges homespec's original authorship and does not imply endorsement by its author.
