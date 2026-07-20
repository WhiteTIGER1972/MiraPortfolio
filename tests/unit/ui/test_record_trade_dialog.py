"""Contract tests for exact BUY and SELL transaction input."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import QDateTime, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from app.application.results import AssetView
from app.domain.entities.asset import AssetType
from app.domain.entities.transaction import TransactionType
from app.domain.value_objects.currency import Currency
from app.ui.dialogs import record_trade_dialog as dialog_module
from app.ui.dialogs.record_trade_dialog import RecordTradeDialog

CREATED_AT = datetime(2026, 7, 20, tzinfo=UTC)
TRADE_QDATETIME = QDateTime.fromString(
    "2026-07-20T12:34:56.789Z",
    Qt.DateFormat.ISODateWithMs,
)


def make_asset(symbol: str, name: str, currency: Currency) -> AssetView:
    """Build a descriptive global Asset option."""
    return AssetView(
        id=uuid4(),
        symbol=symbol,
        name=name,
        asset_type=AssetType.EQUITY,
        currency=currency,
        is_active=True,
        created_at=CREATED_AT,
    )


def set_valid_trade_inputs(dialog: RecordTradeDialog) -> None:
    """Fill every required trade field with exact test values."""
    dialog.findChild(QLineEdit, "tradeQuantityInput").setText("10.000000000000000001")
    dialog.findChild(QLineEdit, "tradeUnitPriceInput").setText("0.000000000000000009")
    dialog.findChild(QLineEdit, "tradeCommissionInput").setText("1.2300")
    dialog.findChild(QLineEdit, "tradeTaxInput").setText("0.0400")
    dialog.findChild(QDateTimeEdit, "tradeDateTimeInput").setDateTime(TRADE_QDATETIME)


@pytest.mark.parametrize(
    ("transaction_type", "title", "button_text"),
    [
        (TransactionType.BUY, "Record buy transaction", "Buy"),
        (TransactionType.SELL, "Record sell transaction", "Sell"),
    ],
)
def test_trade_dialog_direction_asset_order_and_uuid_identity(
    qapplication: QApplication,
    transaction_type: TransactionType,
    title: str,
    button_text: str,
) -> None:
    first = make_asset("DUP", "First", Currency.TRY)
    second = make_asset("DUP", "Second", Currency.USD)
    dialog = RecordTradeDialog(transaction_type, (first, second))
    try:
        asset_combo = dialog.findChild(QComboBox, "tradeAssetInput")
        record_button = dialog.findChild(QPushButton, "recordTradeDialogButton")

        assert dialog.windowTitle() == title
        assert dialog.transaction_type() is transaction_type
        assert asset_combo is not None
        assert asset_combo.count() == 2
        assert [asset_combo.itemText(index) for index in range(2)] == [
            "DUP — First (TRY)",
            "DUP — Second (USD)",
        ]
        assert [asset_combo.itemData(index) for index in range(2)] == [
            str(first.id),
            str(second.id),
        ]
        assert asset_combo.itemData(0) != asset_combo.itemData(1)
        assert dialog.asset_id() == first.id
        assert record_button is not None
        assert record_button.text() == button_text
        assert record_button.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapplication.processEvents()


def test_trade_dialog_exposes_exact_decimals_and_aware_utc_datetime(
    qapplication: QApplication,
) -> None:
    asset = make_asset("EXACT", "Exact values", Currency.EUR)
    dialog = RecordTradeDialog(TransactionType.BUY, (asset,))
    try:
        assert dialog.commission() == Decimal("0")
        assert dialog.tax() == Decimal("0")
        set_valid_trade_inputs(dialog)

        assert dialog.asset_id() == asset.id
        assert dialog.quantity().as_tuple() == Decimal("10.000000000000000001").as_tuple()
        assert dialog.unit_price().as_tuple() == Decimal("0.000000000000000009").as_tuple()
        assert dialog.commission().as_tuple() == Decimal("1.2300").as_tuple()
        assert dialog.tax().as_tuple() == Decimal("0.0400").as_tuple()
        assert dialog.trade_datetime() == datetime(
            2026,
            7,
            20,
            12,
            34,
            56,
            789000,
            tzinfo=UTC,
        )

        QTest.mouseClick(
            dialog.findChild(QPushButton, "recordTradeDialogButton"),
            Qt.MouseButton.LeftButton,
        )
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.close()
        dialog.deleteLater()
        qapplication.processEvents()


@pytest.mark.parametrize(
    ("object_name", "value", "accessor", "message"),
    [
        ("tradeQuantityInput", "", "quantity", "required"),
        ("tradeQuantityInput", "0", "quantity", "greater than zero"),
        ("tradeQuantityInput", "-0.1", "quantity", "greater than zero"),
        ("tradeQuantityInput", "NaN", "quantity", "finite"),
        ("tradeQuantityInput", "Infinity", "quantity", "finite"),
        ("tradeQuantityInput", "-Infinity", "quantity", "finite"),
        ("tradeQuantityInput", "1,25", "quantity", "valid decimal"),
        ("tradeUnitPriceInput", "-0.1", "unit_price", "cannot be negative"),
        ("tradeCommissionInput", "-0.1", "commission", "cannot be negative"),
        ("tradeTaxInput", "-0.1", "tax", "cannot be negative"),
    ],
)
def test_trade_dialog_rejects_invalid_decimal_input(
    qapplication: QApplication,
    object_name: str,
    value: str,
    accessor: str,
    message: str,
) -> None:
    dialog = RecordTradeDialog(
        TransactionType.BUY,
        (make_asset("ONE", "One", Currency.GBP),),
    )
    try:
        set_valid_trade_inputs(dialog)
        dialog.findChild(QLineEdit, object_name).setText(value)

        with pytest.raises(ValueError, match=message):
            getattr(dialog, accessor)()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapplication.processEvents()


def test_invalid_trade_click_keeps_dialog_open_and_cancel_rejects(
    monkeypatch: pytest.MonkeyPatch,
    qapplication: QApplication,
) -> None:
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    dialog = RecordTradeDialog(
        TransactionType.SELL,
        (make_asset("ONE", "One", Currency.TRY),),
    )
    try:
        dialog.show()
        QTest.mouseClick(
            dialog.findChild(QPushButton, "recordTradeDialogButton"),
            Qt.MouseButton.LeftButton,
        )
        assert dialog.isVisible()
        assert warnings == [("Invalid transaction", "Quantity is required.")]

        QTest.mouseClick(
            dialog.findChild(QPushButton, "cancelTradeDialogButton"),
            Qt.MouseButton.LeftButton,
        )
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.close()
        dialog.deleteLater()
        qapplication.processEvents()


def test_trade_dialog_disables_empty_options_and_rejects_non_trade_types(
    qapplication: QApplication,
) -> None:
    dialog = RecordTradeDialog(TransactionType.BUY, ())
    try:
        record_button = dialog.findChild(QPushButton, "recordTradeDialogButton")
        assert record_button is not None
        assert not record_button.isEnabled()
        with pytest.raises(ValueError, match="Asset selection is invalid"):
            dialog.asset_id()
        with pytest.raises(ValueError, match="only BUY and SELL"):
            RecordTradeDialog(TransactionType.DIVIDEND, ())
    finally:
        dialog.close()
        dialog.deleteLater()
        qapplication.processEvents()


def test_trade_dialog_source_has_no_service_persistence_or_float_dependency() -> None:
    source = Path(dialog_module.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "app.application.services",
        "app.infrastructure",
        "sqlalchemy",
        "UnitOfWork",
        "Session",
        "QDoubleSpinBox",
        "float(",
    ):
        assert forbidden not in source
