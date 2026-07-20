"""Exact BUY and SELL transaction input dialog."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from PySide6.QtCore import QDateTime, QTimeZone
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.domain.entities.transaction import TransactionType
from app.ui.dialogs.input_parsing import (
    AssetOption,
    parse_decimal,
    qdatetime_to_utc,
    uuid_from_item_data,
)


class RecordTradeDialog(QDialog):
    """Collect one exact BUY or SELL command payload."""

    def __init__(
        self,
        transaction_type: TransactionType,
        assets: Sequence[AssetOption],
        parent: QWidget | None = None,
    ) -> None:
        if transaction_type not in (TransactionType.BUY, TransactionType.SELL):
            raise ValueError("Trade dialog supports only BUY and SELL transactions.")
        super().__init__(parent)
        self._transaction_type = transaction_type
        action_label = "Buy" if transaction_type is TransactionType.BUY else "Sell"
        self.setWindowTitle(f"Record {action_label.lower()} transaction")
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._asset_combo = QComboBox(self)
        self._asset_combo.setObjectName("tradeAssetInput")
        for asset in assets:
            self._asset_combo.addItem(
                f"{asset.symbol} — {asset.name} ({asset.currency.value})",
                str(asset.id),
            )
        form.addRow("Asset", self._asset_combo)

        self._quantity_input = QLineEdit(self)
        self._quantity_input.setObjectName("tradeQuantityInput")
        self._quantity_input.setPlaceholderText("Quantity")
        form.addRow("Quantity", self._quantity_input)

        self._unit_price_input = QLineEdit(self)
        self._unit_price_input.setObjectName("tradeUnitPriceInput")
        self._unit_price_input.setPlaceholderText("Unit price")
        form.addRow("Unit price", self._unit_price_input)

        self._commission_input = QLineEdit("0", self)
        self._commission_input.setObjectName("tradeCommissionInput")
        form.addRow("Commission", self._commission_input)

        self._tax_input = QLineEdit("0", self)
        self._tax_input.setObjectName("tradeTaxInput")
        form.addRow("Tax", self._tax_input)

        self._trade_datetime_input = QDateTimeEdit(self)
        self._trade_datetime_input.setObjectName("tradeDateTimeInput")
        self._trade_datetime_input.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._trade_datetime_input.setCalendarPopup(True)
        self._trade_datetime_input.setTimeZone(QTimeZone.utc())
        self._trade_datetime_input.setDateTime(QDateTime.currentDateTimeUtc())
        form.addRow("Trade date/time (UTC)", self._trade_datetime_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.setObjectName("tradeDialogButtons")
        record_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        record_button.setText(action_label)
        record_button.setObjectName("recordTradeDialogButton")
        record_button.setDefault(True)
        record_button.setEnabled(bool(assets))
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setObjectName("cancelTradeDialogButton")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._quantity_input.setFocus()

    def transaction_type(self) -> TransactionType:
        """Return the configured trade direction."""
        return self._transaction_type

    def asset_id(self) -> UUID:
        """Return the selected Asset UUID."""
        return uuid_from_item_data(self._asset_combo.currentData())

    def quantity(self) -> Decimal:
        """Return an exact, strictly positive quantity."""
        return parse_decimal(
            self._quantity_input.text(),
            field_name="Quantity",
            strictly_positive=True,
        )

    def unit_price(self) -> Decimal:
        """Return an exact, non-negative unit price."""
        return parse_decimal(self._unit_price_input.text(), field_name="Unit price")

    def commission(self) -> Decimal:
        """Return an exact, non-negative commission."""
        return parse_decimal(self._commission_input.text(), field_name="Commission")

    def tax(self) -> Decimal:
        """Return an exact, non-negative tax."""
        return parse_decimal(self._tax_input.text(), field_name="Tax")

    def trade_datetime(self) -> datetime:
        """Return the user-selected aware UTC timestamp."""
        return qdatetime_to_utc(self._trade_datetime_input.dateTime())

    def _validate_and_accept(self) -> None:
        try:
            self.asset_id()
            self.quantity()
            self.unit_price()
            self.commission()
            self.tax()
            self.trade_datetime()
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, "Invalid transaction", str(error))
            return
        self.accept()


__all__ = ["RecordTradeDialog"]
