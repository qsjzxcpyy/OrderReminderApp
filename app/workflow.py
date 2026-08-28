from collections.abc import Mapping


VIRTUAL_PENDING = "VIRTUAL_PENDING"
WAITING_EXCEPTION = "WAITING_EXCEPTION"
SHIPPED_PENDING_RETURN = "SHIPPED_PENDING_RETURN"
COMPLETED = "COMPLETED"

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
        return SHIPPED_PENDING_RETURN
    notes = " ".join(_text(row.get(field)).lower() for field in _NOTE_FIELDS)
    if any(word in notes for word in _WAITING_WORDS):
        return WAITING_EXCEPTION
    return VIRTUAL_PENDING


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
