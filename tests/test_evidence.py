from spendlens.evidence import recover_receipt_fields
from spendlens.models import ReceiptExtraction


def test_recovers_sams_club_summary_amounts_from_ocr_evidence() -> None:
    extraction = ReceiptExtraction.model_validate(
        {
            "merchant": "Sam's Club",
            "transaction_date": "2026-09-21",
            "subtotal": None,
            "tax": None,
            "total": None,
            "currency": None,
        }
    )
    ocr_text = """
    SUBTOTAL        48.60
    TAX1    6.0000 %     0.60
    TOTAL           49.20
    SAMS CONSUMER CREDIT TEND   49.20
    CHANGE DUE       0.00
    """

    recovered, fields = recover_receipt_fields(
        extraction,
        ocr_text=ocr_text,
        default_currency="USD",
    )

    assert recovered.subtotal is not None
    assert str(recovered.subtotal) == "48.60"
    assert recovered.tax is not None
    assert str(recovered.tax) == "0.60"
    assert recovered.total is not None
    assert str(recovered.total) == "49.20"
    assert recovered.currency == "USD"
    assert fields == ["subtotal", "tax", "total", "currency"]
