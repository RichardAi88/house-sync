"""Purchase orders preserve saved BOQ quantities, prices and revision identity."""

import re
from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

import pytest
from reportlab import rl_config

from housesync.engine import build_house
from housesync.purchasing import purchase_order_pdf, purchase_orders_zip
from housesync.schema import HouseSettings

COMMERCIAL_FIELDS = {"buyer", "supplier", "delivery_site", "order_date", "required_by", "quote_reference", "notes", "prepared_by", "approved_by"}


@pytest.fixture(autouse=True)
def readable_pdf_streams(monkeypatch):
    # ReportLab's uncompressed mode also powers the existing PDF export tests.
    # Keeping this a test setting avoids adding a parser to runtime dependencies.
    monkeypatch.setattr(rl_config, "pageCompression", 0)


@pytest.fixture(scope="module")
def purchasing_house(tmp_path_factory):
    return build_house(HouseSettings(project_name="Purchasing audit <house> & studio"), tmp_path_factory.mktemp("purchasing-house"))


@pytest.fixture(scope="module")
def coated_override_house(tmp_path_factory):
    return build_house(HouseSettings(project_name="Saved three-coat paint design", paint_coats=3,
                                    wastage={"paint": 2.5}, rate_overrides={"PAINT": 11.125},
                                    rate_source="Saved project allowance; not a supplier quotation", rate_date="2026-11-16"),
                       tmp_path_factory.mktemp("purchasing-paint"))


def _unescape_pdf_string(value):
    def escape(match):
        token = match.group(1)
        if re.fullmatch(rb"[0-7]{1,3}", token):
            return bytes([int(token, 8)])
        return {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}.get(token, token)

    return re.sub(rb"\\([0-7]{1,3}|.)", escape, value, flags=re.DOTALL).decode("latin-1")


def _pdf_text(data):
    # ReportLab can split escaped HTML into adjacent Tj operations without a
    # positioning change: "<", "house", ">" must remain "<house>". Join those
    # fragments directly, inserting spaces only at actual line/object breaks.
    blocks = []
    for block in re.findall(rb"\bBT\b(.*?)\bET\b", data, flags=re.DOTALL):
        parts = []
        for token in re.finditer(rb"\(((?:\\.|[^\\()])*)\)\s*Tj|\bT\*", block):
            parts.append(_unescape_pdf_string(token.group(1)) if token.group(1) is not None else " ")
        blocks.append("".join(parts))
    return " ".join(blocks)


def _pdf_fields(data):
    fields = {}
    for match in re.finditer(rb"\d+\s+\d+\s+obj\b(.*?)endobj", data, flags=re.DOTALL):
        body = match.group(1)
        name = re.search(rb"/T\s*\(((?:\\.|[^\\()])*)\)", body)
        if name is not None and re.search(rb"/FT\s*/Tx\b", body):
            fields[_unescape_pdf_string(name.group(1))] = body
    return fields


def _assert_numbers(text, *values):
    numbers = {Decimal(value.replace(",", "")) for value in re.findall(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?(?![\w.])", text)}
    for value in values:
        assert Decimal(str(value)) in numbers, f"Missing saved number {value} in PDF text: {text}"


@pytest.mark.parametrize("code", ["FLOOR", "WALL", "ROOF", "PAINT", "DOOR", "WINDOW", "FLOOR_FINISH", "CEILING", "SKIRTING", "INTERNAL_PLASTER", "EXTERNAL_RENDER"])
def test_each_saved_boq_item_produces_its_own_revision_linked_order(purchasing_house, code):
    result = deepcopy(purchasing_house)
    untouched = deepcopy(result)
    data = purchase_order_pdf(result, code)
    assert isinstance(data, bytes) and data.startswith(b"%PDF-")
    text = _pdf_text(data)
    row = next(item for item in result["boq"] if item["code"] == code)
    assert result["revision"] in text
    assert result["settings"]["project_name"] in text
    assert f"PO-{result['revision']}-{code}" in text
    assert code in text
    assert result["totals"]["currency"] in text
    _assert_numbers(text, row["quantity"], row["rate"], row["amount"])
    assert "draft" in text.lower()
    assert "illustrative" in text.lower()
    assert result == untouched


def test_commercial_and_approval_details_remain_blank_editable_form_fields(purchasing_house):
    data = purchase_order_pdf(purchasing_house, "FLOOR_FINISH")
    assert b"/AcroForm" in data
    fields = _pdf_fields(data)
    assert fields.keys() >= COMMERCIAL_FIELDS
    for name in COMMERCIAL_FIELDS:
        field = fields[name]
        flag_match = re.search(rb"/Ff\s+(\d+)", field)
        flags = int(flag_match.group(1)) if flag_match is not None else 0
        assert flags & 1 == 0, f"{name} unexpectedly marked ReadOnly"
        value = re.search(rb"/V\s*\(((?:\\.|[^\\()])*)\)", field)
        assert value is None or _unescape_pdf_string(value.group(1)).strip() == ""


def test_paint_order_uses_saved_coated_wasted_gross_and_exact_override(coated_override_house):
    result = coated_override_house
    paint = next(row for row in result["boq"] if row["code"] == "PAINT")
    assert paint["measured_quantity"] == 209.4
    assert paint["paint_coats"] == 3
    assert paint["net_quantity"] == 628.2
    assert paint["wastage_percent"] == 2.5
    assert paint["gross_quantity"] == 643.905
    assert paint["rate"] == 11.125
    assert paint["amount"] == 7163.44
    text = _pdf_text(purchase_order_pdf(result, "PAINT"))
    _assert_numbers(text, 209.4, 3, 628.2, 2.5, 643.905, 11.125, 7163.44)
    assert "Saved project allowance" in text
    assert "2026-11-16" in text
    assert "override" in text.lower()


def test_zip_contains_one_named_pdf_per_saved_code_including_paint(purchasing_house):
    data = purchase_orders_zip(purchasing_house)
    assert isinstance(data, bytes)
    with ZipFile(BytesIO(data)) as zipped:
        expected = {f"PO-{purchasing_house['revision']}-{row['code']}.pdf" for row in purchasing_house["boq"]}
        assert set(zipped.namelist()) == expected
        assert len(zipped.namelist()) == len(expected) == 11
        for name in expected:
            pdf = zipped.read(name)
            assert pdf.startswith(b"%PDF-")
            assert purchasing_house["revision"] in _pdf_text(pdf)
        assert f"PO-{purchasing_house['revision']}-PAINT.pdf" in zipped.namelist()


def test_zero_count_order_is_kept_in_single_and_zip_exports(purchasing_house):
    result = deepcopy(purchasing_house)
    door = next(row for row in result["boq"] if row["code"] == "DOOR")
    door.update(measured_quantity=0, net_quantity=0, gross_quantity=0, quantity=0, amount=0)
    text = _pdf_text(purchase_order_pdf(result, "DOOR"))
    _assert_numbers(text, 0, door["rate"])
    with ZipFile(BytesIO(purchase_orders_zip(result))) as zipped:
        assert f"PO-{result['revision']}-DOOR.pdf" in zipped.namelist()


def test_legacy_saved_boq_without_new_optional_fields_preserves_original_price():
    result = {"revision": "legacy123abc", "settings": {"project_name": "Historical SGD house", "currency": "SGD"},
              "boq": [{"code": "PAINT", "description": "Retained single measured paint quantity", "quantity": 200,
                       "unit": "m2", "rate": 12, "amount": 2400}],
              "totals": {"currency": "SGD", "subtotal": 2400}}
    untouched = deepcopy(result)
    text = _pdf_text(purchase_order_pdf(result, "PAINT"))
    assert "Historical SGD house" in text
    assert result["revision"] in text and "SGD" in text
    _assert_numbers(text, 200, 12, 2400)
    assert "AUD" not in text
    assert result == untouched


def test_unknown_valid_code_is_not_silently_substituted(purchasing_house):
    with pytest.raises(KeyError):
        purchase_order_pdf(purchasing_house, "UNMODELLED_TRADE")


@pytest.mark.parametrize("code", ["../PAINT", "PAINT\n", "paint", "", "A" * 81])
def test_invalid_order_codes_cannot_become_unsafe_filenames(purchasing_house, code):
    with pytest.raises(ValueError):
        purchase_order_pdf(purchasing_house, code)


@pytest.mark.parametrize("revision", ["../../other-folder", "invalid revision"])
def test_unsafe_revision_identity_is_rejected_instead_of_used_in_zip_name(purchasing_house, revision):
    result = deepcopy(purchasing_house)
    result["revision"] = revision
    with pytest.raises(ValueError):
        purchase_orders_zip(result)


def test_duplicate_saved_codes_are_rejected_instead_of_overwriting_orders(purchasing_house):
    result = deepcopy(purchasing_house)
    result["boq"].append(deepcopy(result["boq"][0]))
    with pytest.raises(ValueError):
        purchase_orders_zip(result)
