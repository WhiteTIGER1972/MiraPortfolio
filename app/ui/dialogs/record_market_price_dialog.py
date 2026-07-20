"""Exact manual market-price input dialog."""

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

from app.ui.dialogs.input_parsing import (
    AssetOption,
    parse_decimal,
    qdatetime_to_utc,
    uuid_from_item_data,
)


class RecordMarketPriceDialog(QDialog):
    """Collect one exact manual Asset price observation."""

    def __init__(
        self,
        assets: Sequence[AssetOption],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Record market price")
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._asset_combo = QComboBox(self)
        self._asset_combo.setObjectName("priceAssetInput")
        for asset in assets:
            self._asset_combo.addItem(
                f"{asset.symbol} — {asset.name} ({asset.currency.value})",
                str(asset.id),
            )
        form.addRow("Asset", self._asset_combo)

        self._price_input = QLineEdit(self)
        self._price_input.setObjectName("marketPriceInput")
        self._price_input.setPlaceholderText("Price")
        form.addRow("Price", self._price_input)

        self._observed_at_input = QDateTimeEdit(self)
        self._observed_at_input.setObjectName("priceObservedAtInput")
        self._observed_at_input.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._observed_at_input.setCalendarPopup(True)
        self._observed_at_input.setTimeZone(QTimeZone.utc())
        self._observed_at_input.setDateTime(QDateTime.currentDateTimeUtc())
        form.addRow("Observed date/time (UTC)", self._observed_at_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.setObjectName("priceDialogButtons")
        record_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        record_button.setText("Record")
        record_button.setObjectName("recordPriceDialogButton")
        record_button.setDefault(True)
        record_button.setEnabled(bool(assets))
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setObjectName("cancelPriceDialogButton")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._price_input.setFocus()

    def asset_id(self) -> UUID:
        """Return the selected Asset UUID."""
        return uuid_from_item_data(self._asset_combo.currentData())

    def price(self) -> Decimal:
        """Return an exact, finite, non-negative price."""
        return parse_decimal(self._price_input.text(), field_name="Price")

    def observed_at(self) -> datetime:
        """Return the user-selected aware UTC observation timestamp."""
        return qdatetime_to_utc(self._observed_at_input.dateTime())

    def _validate_and_accept(self) -> None:
        try:
            self.asset_id()
            self.price()
            self.observed_at()
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, "Invalid market price", str(error))
            return
        self.accept()


__all__ = ["RecordMarketPriceDialog"]
