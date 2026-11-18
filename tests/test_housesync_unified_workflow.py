"""Compiled, saved-revision integration across rooms, purchasing and client quotes."""

from __future__ import annotations

import csv
import json
import re
import threading
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import ifcopenshell
import pytest

from housesync import server
from housesync.schema import HouseSettings


def _pdf_text(data):
    """Extract the exporters' uncompressed ReportLab literal text operators."""
    def unescape(match):
        value = match.group(1)
        if re.fullmatch(rb"[0-7]{1,3}", value):
            return bytes([int(value, 8)])
        return {b"n": b"\n", b"r": b"\r", b"t": b"\t"}.get(value, value)

    blocks = []
    for block in re.findall(rb"\bBT\b(.*?)\bET\b", data, re.DOTALL):
        parts = []
        for token in re.finditer(rb"\(((?:\\.|[^\\()])*)\)\s*Tj|\bT\*", block):
            value = token.group(1)
            parts.append(" " if value is None else re.sub(rb"\\([0-7]{1,3}|.)", unescape, value).decode("latin-1"))
        blocks.append("".join(parts))
    return " ".join(blocks)


def _money(value):
    return Decimal(str(value))


def _quote_totals(comparison, **expected):
    totals = comparison["quote"]["public"]["totals"]
    for key, value in expected.items():
        assert _money(totals[key]) == _money(value), (key, totals[key], value)
    return totals


@pytest.fixture(scope="module")
def unified_workflow(tmp_path_factory):
    root = tmp_path_factory.mktemp("unified-room-purchasing-quote")
    library = root / "rates.csv"
    library.write_bytes((Path(__file__).resolve().parents[1] / "rates" / "rates_aud.csv").read_bytes())
    application = server.Application(root / "issued", rate_library_path=library)
    httpd = server.LocalHTTPServer(("127.0.0.1", 0), server.handler_for(application))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"

    def fetch(path, values=None):
        request = Request(base + path, data=None if values is None else json.dumps(values).encode(),
                          headers={} if values is None else {"Content-Type": "application/json"})
        with urlopen(request, timeout=180) as response:
            return response.headers.get_content_type(), response.read()

    def api(path, values=None):
        mime, body = fetch(path, values)
        assert mime == "application/json"
        return json.loads(body)

    try:
        before = api("/api/build", HouseSettings().model_dump(mode="json"))
        enlarged = api("/api/build", HouseSettings(length_mm=12000).model_dump(mode="json"))
        split_inputs = HouseSettings().model_dump(mode="json")
        split_inputs["partitions"] = [{"id": "P1", "start_mm": [4000, 0], "end_mm": [4000, 8000],
                                       "thickness_mm": 100, "height_mm": 3000, "wall_type": "100 mm partition"}]
        split_inputs["openings"].append({"id": "D2", "kind": "door", "host": "P1", "offset_mm": 1500,
                                           "width_mm": 1000, "height_mm": 2100, "sill_mm": 0})
        layout = api("/api/layout", split_inputs)
        assert layout["checks"]["errors"] == []
        split_inputs["rooms"] = [{"id": room["id"], "name": "Study" if room["label_point_mm"][0] < 4000 else "Living",
                                  "finishes": {"floor": "Selected oak floor", "paint": "Selected warm white"}}
                                 for room in layout["rooms"]]
        split_inputs["rate_overrides"] = {"FLOOR_FINISH": 65}
        split_inputs["rate_source"] = "PRIVATE_UNIFIED_RATE_JUSTIFICATION_5C24"
        split = api("/api/build", split_inputs)
        allowance_inputs = deepcopy(split_inputs)
        allowance_inputs["allowances"] = [
            {"id": "AL1", "kind": "prime_cost", "description": "Client-selected fixtures", "amount": 500,
             "note": "Final selection remains to be confirmed."},
            {"id": "AL2", "kind": "provisional_sum", "description": "Unmeasured site work", "amount": 1000,
             "note": "Actual scope and cost remain to be confirmed."},
        ]
        with_allowances = api("/api/build", allowance_inputs)
        yield {"root": root, "library": library, "application": application, "fetch": fetch, "api": api,
               "before": before, "enlarged": enlarged, "split": split, "allowances": with_allowances}
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
        assert not thread.is_alive()


def _comparison(flow, before, after):
    return flow["api"](f"/api/compare?before={before['revision']}&after={after['revision']}")


def _quote_pdfs(flow, comparison):
    files = {}
    for key in ("client_quote_pdf", "internal_breakdown_pdf"):
        mime, content = flow["fetch"](comparison["files"][key])
        assert mime == "application/pdf" and content.startswith(b"%PDF-")
        files[key] = content
    return files


def test_room_split_saved_geometry_boq_and_ifc_share_one_revision(unified_workflow):
    flow = unified_workflow
    split = flow["split"]
    assert split["totals"] == {"currency": "AUD", "subtotal": 57038.34}
    assert len(split["boq"]) == 18
    # Independent geometry: 0.1x8 partition, one 1x2.1 opening; the
    # threshold remains excluded from the two clear finish footprints.
    quantities = split["quantities"]
    assert quantities["floor_area_m2"] == 80
    assert quantities["floor_finish_area_m2"] == pytest.approx(80 - .1 * 8)
    assert quantities["partition_wall_volume_m3"] == pytest.approx(.1 * (8 * 3 - 1 * 2.1))
    assert quantities["internal_plaster_area_m2"] == pytest.approx((23.9 + 27.9) * 3 - 5.7 - 2 * 2.1)
    assert quantities["paint_area_m2"] == pytest.approx(145.5 + 107.1)
    assert quantities["skirting_length_m"] == pytest.approx(23.9 + 27.9 - 1 - 2)
    rooms = {room["name"]: room for room in split["rooms"]}
    assert rooms["Study"]["area_m2"] == pytest.approx(3.95 * 8)
    assert rooms["Living"]["area_m2"] == pytest.approx(5.95 * 8)
    assert all(room["reachable"] for room in rooms.values())
    assert sum(_money(row["amount"]) for row in split["boq"]) == _money(57038.34)
    assert sum(_money(group["subtotal"]) for group in split["boq_groups"]) == _money(57038.34)
    for room in rooms.values():
        rows = {row["rate_code"]: row for row in split["boq"] if row["group_id"] == room["id"]}
        assert set(rows) == {"FLOOR_FINISH", "CEILING", "SKIRTING", "INTERNAL_PLASTER", "PAINT"}
        assert rows["FLOOR_FINISH"]["gross_quantity"] == pytest.approx(room["area_m2"] * 1.1)
        assert rows["PAINT"]["net_quantity"] == pytest.approx(room["paint_area_m2"] * 2)
    mime, data = flow["fetch"](split["files"]["ifc"])
    assert data and mime in {"application/octet-stream", "application/x-step", "model/ifc"}
    model = ifcopenshell.file.from_string(data.decode())
    assert {space.Name for space in model.by_type("IfcSpace")} == {room["id"] for room in rooms.values()}
    assert {space.LongName for space in model.by_type("IfcSpace")} == {"Study", "Living"}
    assert len(model.by_type("IfcWall")) == 5 and len(model.by_type("IfcDoor")) == 2
    for key in ("boq_csv", "boq_xlsx", "specification_docx", "specification_pdf", "drawings_pdf"):
        assert split["revision"] in split["files"][key]
        assert flow["fetch"](split["files"][key])[1]


def test_each_grouped_purchase_order_copies_saved_gross_rate_and_amount(unified_workflow):
    flow, split = unified_workflow, unified_workflow["split"]
    mime, bundle = flow["fetch"](f"/purchase-orders/{split['revision']}/all.zip")
    assert mime == "application/zip"
    with ZipFile(BytesIO(bundle)) as archive:
        assert len(archive.namelist()) == 18
        for row in split["boq"]:
            filename = f"PO-{split['revision']}-{row['code']}.pdf"
            mime, data = flow["fetch"](f"/purchase-orders/{split['revision']}/{row['code']}.pdf")
            assert mime == "application/pdf" and archive.read(filename) == data
            text = _pdf_text(data)
            assert split["revision"] in text and row["group_name"] in text and row["code"] in text
            numbers = {_money(value.replace(",", "")) for value in re.findall(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?(?![\w.])", text)}
            assert {_money(row[key]) for key in ("gross_quantity", "rate", "amount")} <= numbers
            assert b"/AcroForm" in data
    study = next(room for room in split["rooms"] if room["name"] == "Study")
    floor = next(row for row in split["boq"] if row["group_id"] == study["id"] and row["rate_code"] == "FLOOR_FINISH")
    assert (floor["net_quantity"], floor["wastage_percent"], floor["gross_quantity"], floor["rate"], floor["amount"]) == (31.6, 10, 34.76, 65, 2259.4)


def test_ten_to_twelve_quote_and_both_pdfs_use_the_saved_boq_pair(unified_workflow):
    flow = unified_workflow
    before, enlarged = flow["before"], flow["enlarged"]
    assert before["totals"]["subtotal"] == 53330.22
    assert enlarged["totals"]["subtotal"] == 61640.22
    comparison = _comparison(flow, before, enlarged)
    assert comparison["totals"]["net_change"] == 8310
    _quote_totals(comparison, additions_at_cost=8310, credits_at_cost=0, at_cost_delta=8310,
                  markup_amount=1662, admin_fee=0, subtotal_ex_gst=9972, gst_amount=997.2, total_incl_gst=10969.2)
    for data in _quote_pdfs(flow, comparison).values():
        text = _pdf_text(data)
        assert before["revision"] in text and enlarged["revision"] in text
        assert comparison["quote"]["public"]["quote_reference"] in text
        assert all(amount in text for amount in ("8,310.00", "1,662.00", "9,972.00", "997.20", "10,969.20"))


def test_split_quote_allowances_credits_and_private_fields_reconcile(unified_workflow):
    flow = unified_workflow
    before, split, allowances = flow["before"], flow["split"], flow["allowances"]
    assert allowances["boq"] == split["boq"] and allowances["quantities"] == split["quantities"]
    comparison = _comparison(flow, before, split)
    assert comparison["totals"]["net_change"] == 3708.12
    _quote_totals(comparison, additions_at_cost=3803.12, credits_at_cost=95, at_cost_delta=3708.12,
                  markup_amount=760.62, admin_fee=0, subtotal_ex_gst=4468.74, gst_amount=446.87, total_incl_gst=4915.61)
    with_allowances = _comparison(flow, before, allowances)
    _quote_totals(with_allowances, additions_at_cost=5303.12, credits_at_cost=95, at_cost_delta=5208.12,
                  markup_amount=1060.62, subtotal_ex_gst=6268.74, gst_amount=626.87, total_incl_gst=6895.61)
    public = with_allowances["quote"]["public"]
    assert [row["movement"] for row in public["pc_allowances"]] == [500]
    assert [row["movement"] for row in public["ps_allowances"]] == [1000]
    reverse = _comparison(flow, allowances, before)
    _quote_totals(reverse, additions_at_cost=95, credits_at_cost=5303.12, at_cost_delta=-5208.12,
                  markup_amount=19, subtotal_ex_gst=-5189.12, gst_amount=-518.91, total_incl_gst=-5708.03)
    documents = _quote_pdfs(flow, with_allowances)
    client, internal = (_pdf_text(documents[key]) for key in ("client_quote_pdf", "internal_breakdown_pdf"))
    assert "Client-selected fixtures" in client and "Unmeasured site work" in client and "6,895.61" in client
    secret = split["settings"]["rate_source"]
    assert secret in internal and secret not in json.dumps(public) and secret not in client
    assert split["rate_library"]["sha256"] not in client
    assert all(row["code"] not in client for row in split["boq"] if row["code"].startswith("ROOM_"))


def test_invalid_access_preserves_the_issued_snapshot(unified_workflow):
    flow = unified_workflow
    issued = flow["application"].state()
    original = (flow["application"].output_root / issued["revision"] / "result.json").read_bytes()
    invalid = deepcopy(flow["split"]["settings"])
    invalid["openings"] = [opening for opening in invalid["openings"] if opening["id"] != "D2"]
    with pytest.raises(HTTPError) as error:
        flow["api"]("/api/build", invalid)
    assert error.value.code == 422
    assert flow["application"].state()["revision"] == issued["revision"]
    assert (flow["application"].output_root / issued["revision"] / "result.json").read_bytes() == original


def test_old_grouped_orders_and_quote_pdfs_survive_later_library_model_and_clock_edits(unified_workflow, monkeypatch):
    flow = unified_workflow
    before, split = flow["before"], flow["split"]
    comparison = _comparison(flow, before, split)
    original_quote, original_pdfs = deepcopy(comparison["quote"]), _quote_pdfs(flow, comparison)
    original_bundle = flow["fetch"](f"/purchase-orders/{split['revision']}/all.zip")[1]
    snapshots = {result["revision"]: (flow["application"].output_root / result["revision"] / "result.json").read_bytes()
                 for result in (before, split)}
    future = date.fromisoformat(original_quote["public"]["issued_on"]) + timedelta(days=45)
    monkeypatch.setattr(server, "_quote_issue_date", lambda: future)
    with flow["library"].open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers, rows = reader.fieldnames, list(reader)
    next(row for row in rows if row["code"] == "FLOOR_FINISH")["rate"] = "99"
    with flow["library"].open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    later = flow["api"]("/api/build", HouseSettings(length_mm=11000, paint_colour="Later white", paint_hex="#FAF9F6",
                                                   builder_pricing={"overhead_margin_percent": 91, "admin_fee_aud": 999}).model_dump(mode="json"))
    assert later["revision"] not in snapshots
    assert _comparison(flow, before, later)["quote"]["public"]["issued_on"] == future.isoformat()
    reopened = server.Application(flow["application"].output_root, rate_library_path=flow["library"])
    restored = reopened.compare(before["revision"], split["revision"])
    assert restored["comparison_id"] == comparison["comparison_id"] and restored["quote"] == original_quote
    assert _quote_pdfs(flow, restored) == original_pdfs
    assert flow["fetch"](f"/purchase-orders/{split['revision']}/all.zip")[1] == original_bundle
    for revision, data in snapshots.items():
        assert (reopened.output_root / revision / "result.json").read_bytes() == data
