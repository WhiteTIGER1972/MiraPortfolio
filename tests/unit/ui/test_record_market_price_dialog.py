"""Contract tests for exact manual market-price input."""

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
    QPushButton,
)

from app.application.results import AssetView
from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency
from app.ui.dialogs import record_market_price_dialog as dialog_module
from app.ui.dialogs.record_market_price_dialog import RecordMarketPriceDialog

OBSERVED_QDATETIME = QDateTime.fromString(
    "2026-07-20T08:07:06.543Z",
    Qt.DateFormat.ISODateWithMs,
)


def make_asset(symbol: str, name: str, currency: Currency) -> AssetView:
    """Build a global Asset option."""
    return AssetView(
        id=uuid4(),
        symbol=symbol,
        name=name,
        asset_type=AssetType.EQUITY,
        currency=currency,
        is_active=True,
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
    )


def test_price_dialog_preserves_asset_order_uuid_and_exact_values(
    qapplication: QApplication,
) -> None:
    first = make_asset("DUP", "First", Currency.TRY)
    second = make_asset("DUP", "Second", Currency.USD)
    dialog = RecordMarketPriceDialog((first, second))
    try:
        combo = dialog.findChild(QComboBox, "priceAssetInput")
        price_input = dialog.findChild(QLineEdit, "marketPriceInput")
        observed_input = dialog.findChild(QDateTimeEdit, "priceObservedAtInput")
        assert combo is not None
        assert combo.count() == 2
        assert [combo.itemText(index) for index in range(2)] == [
            "DUP — First (TRY)",
            "DUP — Second (USD)",
        ]
        assert [combo.itemData(index) for index in range(2)] == [
            str(first.id),
            str(second.id),
        ]
        combo.setCurrentIndex(1)
        price_input.setText("123.45000000000000000100")
        observed_input.setDateTime(OBSERVED_QDATETIME)

        assert dialog.asset_id() == second.id
        assert dialog.price().as_tuple() == Decimal("123.45000000000000000100").as_tuple()
        assert dialog.observed_at() == datetime(
            2026,
            7,
            20,
            8,
            7,
            6,
            543000,
            tzinfo=UTC,
        )
        QTest.mouseClick(
            dialog.findChild(QPushButton, "recordPriceDialogButton"),
            Qt.MouseButton.LeftButton,
        )
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.close()
        dialog.deleteLater()
        qapplication.processEvents()


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("0", None),
        ("-0.1", "cannot be negative"),
        ("NaN", "finite"),
        ("Infinity", "finite"),
        ("-Infinity", "finite"),
        ("1,25", "valid decimal"),
    ],
)
def test_price_decimal_policy(
    qapplication: QApplication,
    value: str,
    message: str | None,
) -> None:
    dialog = RecordMarketPriceDialog((make_asset("ONE", "One", Currency.EUR),))
    try:
        dialog.findChild(QLineEdit, "marketPriceInput").setText(value)
        if message is None:
            assert dialog.price() == Decimal("0")
        else:
            with pytest.raises(ValueError, match=message):
                dialog.price()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapplication.processEvents()


def test_price_dialog_disables_record_without_assets_and_cancel_rejects(
    qapplication: QApplication,
) -> None:
    dialog = RecordMarketPriceDialog(())
    try:
        record_button = dialog.findChild(QPushButton, "recordPriceDialogButton")
        cancel_button = dialog.findChild(QPushButton, "cancelPriceDialogButton")
        assert record_button is not None
        assert not record_button.isEnabled()
        assert cancel_button is not None
        QTest.mouseClick(cancel_button, Qt.MouseButton.LeftButton)
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.close()
        dialog.deleteLater()
        qapplication.processEvents()


def test_price_dialog_source_has_no_service_persistence_or_float_dependency() -> None:
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
