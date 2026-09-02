from collections.abc import Mapping


OVERSELL_CUSTOMER_UNSHIPPED = "OVERSELL_CUSTOMER_UNSHIPPED"
OVERSELL_CUSTOMER_VIRTUAL = "OVERSELL_CUSTOMER_VIRTUAL"
OVERSELL_CUSTOMER_REFUNDED = "OVERSELL_CUSTOMER_REFUNDED"
VIRTUAL_PENDING_RETURN = "VIRTUAL_PENDING_RETURN"
VIRTUAL_CUSTOMER_FOLLOWUP = "VIRTUAL_CUSTOMER_FOLLOWUP"
DROPSHIP_PENDING_RETURN = "DROPSHIP_PENDING_RETURN"
COMPLETED = "COMPLETED"
MANUAL_IMPORT_PENDING_RETURN = "MANUAL_IMPORT_PENDING_RETURN"

ALL_STAGES = (
    OVERSELL_CUSTOMER_UNSHIPPED,
    OVERSELL_CUSTOMER_VIRTUAL,
    OVERSELL_CUSTOMER_REFUNDED,
    VIRTUAL_PENDING_RETURN,
    VIRTUAL_CUSTOMER_FOLLOWUP,
    DROPSHIP_PENDING_RETURN,
    COMPLETED,
    MANUAL_IMPORT_PENDING_RETURN,
)
TERMINAL_STAGES = {OVERSELL_CUSTOMER_REFUNDED, COMPLETED}
LEGACY_STAGE_MAP = {
    "NEEDS_ARRIVAL_DATE": OVERSELL_CUSTOMER_UNSHIPPED,
    "VIRTUAL_PENDING": VIRTUAL_PENDING_RETURN,
    "WAITING_EXCEPTION": VIRTUAL_CUSTOMER_FOLLOWUP,
    "SHIPPED_PENDING_RETURN": DROPSHIP_PENDING_RETURN,
}

# Keep old imports and reminder fixtures compatible while data migrates.
VIRTUAL_PENDING = VIRTUAL_PENDING_RETURN
WAITING_EXCEPTION = VIRTUAL_CUSTOMER_FOLLOWUP
SHIPPED_PENDING_RETURN = DROPSHIP_PENDING_RETURN

_NOTE_FIELDS = ("abnormal_reason", "operator_note", "system_note")
_WAITING_WORDS = (
    "abnormal", "exception", "waiting", "wait for", "backorder",
    "waiting-for-stock", "out-of-stock", "problem",
)


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def suggest_stage(row: Mapping) -> str:
    """Suggest an ERP stage without changing the user-selected stage."""
    if _text(row.get("tracking_number")):
        shipping = _text(row.get("shipping_method_name_cn") or row.get("shipping_method_name_en")).lower()
        return DROPSHIP_PENDING_RETURN if not shipping or "代发" in shipping or "drop ship" in shipping else VIRTUAL_PENDING_RETURN
    notes = " ".join(_text(row.get(field)).lower() for field in _NOTE_FIELDS)
    if any(word in notes for word in _WAITING_WORDS):
        return VIRTUAL_CUSTOMER_FOLLOWUP
    return OVERSELL_CUSTOMER_UNSHIPPED


def confirm_return(actual_tracking_number: str) -> dict:
    tracking = _text(actual_tracking_number)
    if not tracking:
        raise ValueError("actual tracking number is required before return confirmation")
    return {
        "stage": COMPLETED,
        "actual_tracking_number": tracking,
        "return_confirmed": True,
    }


def complete_exception(processing_result: str, processing_note: str) -> dict:
    result = _text(processing_result)
    note = _text(processing_note)
    if not result:
        raise ValueError("processing result is required to complete an exception")
    if not note:
        raise ValueError("processing note is required to complete an exception")
    return {
        "stage": COMPLETED,
        "processing_result": result,
        "processing_note": note,
    }
