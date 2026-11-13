# House Sync

A house model with a coordinated bill of quantities, technical specification, and dimensioned drawings. Change dimensions or the paint finish in the local browser editor, then select **Update house & documents** to regenerate the complete set from one shared model revision.

Built on [homespec](https://github.com/maccman/homespec) by Alex MacCaw. The original MIT licence, source, examples and Git history are retained. Read the [original homespec README](HOMESPEC_README.md) for its compiler and Blender workflows.

## First version

- A single-storey, single-room rectangular shell with a flat roof, one door, and two windows.
- Editable clear internal length/width, wall height/thickness, paint name/colour, currency, and five illustrative unit rates.
- An interactive 3D model: orbit, zoom, select elements, and show/hide the roof.
- BOQ in Excel/CSV; technical specification in Word/PDF; floor plan and south/east elevations in SVG/PDF; model in IFC.
- Save/reopen a design as JSON. Previous generated revisions remain available locally.
- Every output identifies the same content-based revision. Pending edits disable downloads until regeneration.
- A **Changes & cost** tab compares saved revision A with revision B, records descriptions of the changes, and shows before/after BOQ quantities, rates, amounts, additions, omissions and the net estimate difference. Reports are available in Excel, CSV, PDF and JSON.

## Capture a change from A to B

Every successful update preserves the previous design and automatically creates a comparison with the new design. Open **Changes & cost** to review the last transition, or select any two saved revisions and choose **Compare revisions**. Each report identifies its two source revisions and is saved locally alongside the revision history. Reopening the application preserves these records.

Select **Export variation order PDF** to download a draft variation order for the selected A-to-B comparison. It includes a VO reference, source revisions, change descriptions, itemised quantities/rates and costs, additions/omissions, the net variation and revised estimate, plus contract reference and approval/signature fields. Exporting leaves the order as a draft for review and approval; it does not approve a variation or assess programme impact.

The default 10 x 8 m design has an illustrative subtotal of SGD 28,574.80. Increasing its length to 12 m at the same rates gives SGD 33,022.80, a **SGD 4,448.00 increase**. A paint colour change at the same quantity and rate is recorded with **zero cost difference**; the application does not invent a product price for a selected screen colour.

The comparison separates quantity effects from rate effects and reconciles them to rounded BOQ amounts. Positive and negative item differences are shown as additions and omissions. Different currencies cannot be compared without an explicit exchange-rate policy. This is an estimate comparison of the modelled BOQ scope; demolition, rework, abortive work, fees, taxes and programme impacts are excluded unless added to the model and rates. It is not an approved contractual variation valuation.

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

## A change you can verify

The default house has **10 x 8 m clear internal dimensions**, 200 mm walls and 3 m wall height. Its compiled floor area is **80 m2**, net wall volume **20.94 m3**, painted area **209.4 m2**, and flat roof area **99 m2**. The illustrative subtotal is **SGD 28,574.80**.

Changing only the length to 12 m increases the clear floor area to **96 m2** and painted wall area to **233.4 m2**. Changing only the paint colour updates the model and finish descriptions while preserving measured quantities. Rates can be edited independently.

## Measurement and document scope

Model dimensions are in millimetres; browser geometry is in metres. Drawings distinguish internal clear dimensions from external overall dimensions. Wall corners are counted once. Painting includes internal and external net vertical faces, deducting all modelled opening voids on both faces. Reveals, wastage, coat multipliers and floor/roof paint are excluded. The roof includes a 300 mm overhang. Door/window rows share an illustrative allowance per opening.

This is a concept application. The BOQ covers modelled items and illustrative rates, not a complete tender estimate. The specification is a technical schedule for development alongside a contract, not a complete contract. Structure, foundations, drainage, services, waterproofing, statutory compliance, workmanship systems and supplier rates require project-specific development. The demonstration does not claim that homespec's building guideline or regulatory checks have been run.

## Files and development

| Path | Purpose |
|---|---|
| `housesync/schema.py` | Validated house parameters and unit rates |
| `housesync/engine.py` | Homespec geometry/IR, quantities, BOQ, IFC and browser mesh |
| `housesync/documents.py` | Coordinated Excel, Word, PDF and SVG exports |
| `housesync/changes.py` | Described A-to-B revision comparison and change-cost report exports |
| `housesync/server.py` | Local server and immutable revision publication |
| `housesync/static/` | Browser editor and Three.js viewer |
| `out/house-sync/<revision>/` | Generated files; excluded from Git |
| [Project plan](docs/house-sync-plan.md) | Scope, verification and next features |
| [Decisions](docs/house-sync-decisions.md) | Geometry, measurement and revision decisions |

~~~powershell
.venv\Scripts\pytest.exe tests/test_housesync_engine.py tests/test_housesync_documents.py tests/test_housesync_server.py tests/test_housesync_changes.py -q
.venv\Scripts\ruff.exe check housesync
node --check housesync/static/app.js
~~~

Windows uses build123d 0.10 to avoid an import failure caused by scanning an invalid Windows font with 0.11. The generation lock includes a Windows msvcrt implementation. Linux/macOS use build123d 0.11, preserving the advanced geometry API and Python 3.13 compatibility used by the upstream test suite.

The upstream library_room build was verified on this PC with **51 checks passed, zero failed**. Local tests independently compare quantities with rectangular-shell calculations and IFC geometry. Generated PDFs and the recalculated spreadsheet were visually inspected. Word package/content tests pass; native Word/LibreOffice visual rendering was unavailable. Six upstream reliability tests that create symbolic links were blocked by the Windows account's symlink privilege; they are not reported as passes.

## Licence

MIT. See [LICENSE](LICENSE). Dependencies retain their respective licences. This project acknowledges homespec's original authorship and does not imply endorsement by its author.
