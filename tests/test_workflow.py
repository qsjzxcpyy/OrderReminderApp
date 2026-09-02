import pytest

from app.workflow import (
    ALL_STAGES,
    COMPLETED,
    DROPSHIP_PENDING_RETURN,
    OVERSELL_CUSTOMER_REFUNDED,
    OVERSELL_CUSTOMER_UNSHIPPED,
    OVERSELL_CUSTOMER_VIRTUAL,
    SHIPPED_PENDING_RETURN,
    VIRTUAL_CUSTOMER_FOLLOWUP,
    VIRTUAL_PENDING_RETURN,
    VIRTUAL_PENDING,
    WAITING_EXCEPTION,
    complete_exception,
    confirm_return,
    suggest_stage,
)


def test_tracking_number_suggests_shipped_pending_return():
    assert suggest_stage({"tracking_number": "1Z999"}) == SHIPPED_PENDING_RETURN


def test_exception_notes_suggest_waiting_exception():
    assert suggest_stage({"tracking_number": "", "operator_note": "waiting-for-stock"}) == WAITING_EXCEPTION


def test_empty_tracking_and_notes_suggest_virtual_pending():
    assert suggest_stage({"tracking_number": "", "operator_note": ""}) == OVERSELL_CUSTOMER_UNSHIPPED


def test_user_selected_stage_is_separate_from_erp_suggestion():
    row = {"tracking_number": "", "operator_note": "waiting-for-stock", "stage": VIRTUAL_PENDING}
    assert suggest_stage(row) == WAITING_EXCEPTION
    assert row["stage"] == VIRTUAL_PENDING


def test_all_order_stages_are_the_seven_manual_workflow_stages():
    assert ALL_STAGES == (
        OVERSELL_CUSTOMER_UNSHIPPED,
        OVERSELL_CUSTOMER_VIRTUAL,
        OVERSELL_CUSTOMER_REFUNDED,
        VIRTUAL_PENDING_RETURN,
        VIRTUAL_CUSTOMER_FOLLOWUP,
        DROPSHIP_PENDING_RETURN,
        COMPLETED,
    )


def test_confirm_return_requires_actual_tracking_number():
    with pytest.raises(ValueError, match="tracking"):
        confirm_return("")


def test_confirm_return_returns_completed_transition_data():
    transition = confirm_return("YT123")
    assert transition == {
        "stage": COMPLETED,
        "actual_tracking_number": "YT123",
        "return_confirmed": True,
    }


def test_exception_completion_requires_result_and_note():
    with pytest.raises(ValueError, match="processing result"):
        complete_exception("", "resolved")
    with pytest.raises(ValueError, match="processing note"):
        complete_exception("resolved", "")


def test_exception_completion_returns_completed_transition_data():
    transition = complete_exception("resolved", "Stock received and shipped")
    assert transition == {
        "stage": COMPLETED,
        "processing_result": "resolved",
        "processing_note": "Stock received and shipped",
    }
