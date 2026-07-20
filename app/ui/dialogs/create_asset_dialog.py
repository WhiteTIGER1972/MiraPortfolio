"""Asset creation input dialog."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency

_ASSET_TYPE_CHOICES = (
    ("Equity", AssetType.EQUITY),
    ("Fund", AssetType.FUND),
    ("ETF", AssetType.ETF),
    ("Bond", AssetType.BOND),
    ("Crypto", AssetType.CRYPTO),
    ("Cash", AssetType.CASH),
)
_CURRENCY_CHOICES = tuple((currency.value, currency) for currency in Currency)


class CreateAssetDialog(QDialog):
    """Collect the descriptive fields required by the Asset use case."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add asset")
        self.setModal(True)
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._symbol_input = QLineEdit(self)
        self._symbol_input.setObjectName("assetSymbolInput")
        self._symbol_input.setPlaceholderText("e.g. MSFT")
        form.addRow("Symbol", self._symbol_input)

        self._name_input = QLineEdit(self)
        self._name_input.setObjectName("assetNameInput")
        self._name_input.setPlaceholderText("Instrument name")
        form.addRow("Name", self._name_input)

        self._asset_type_combo = QComboBox(self)
        self._asset_type_combo.setObjectName("assetTypeInput")
        for label, asset_type in _ASSET_TYPE_CHOICES:
            self._asset_type_combo.addItem(label, asset_type.value)
        form.addRow("Asset type", self._asset_type_combo)

        self._currency_combo = QComboBox(self)
        self._currency_combo.setObjectName("assetCurrencyInput")
        for label, currency in _CURRENCY_CHOICES:
            self._currency_combo.addItem(label, currency.value)
        form.addRow("Currency", self._currency_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.setObjectName("assetDialogButtons")
        create_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        create_button.setText("Create")
        create_button.setObjectName("createAssetDialogButton")
        create_button.setDefault(True)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setObjectName("cancelAssetDialogButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._symbol_input.setFocus()

    def symbol(self) -> str:
        """Return the entered symbol."""
        return self._symbol_input.text()

    def asset_name(self) -> str:
        """Return the entered display name."""
        return self._name_input.text()

    def asset_type(self) -> AssetType:
        """Return the enum represented by the selected item data."""
        value = self._asset_type_combo.currentData()
        if not isinstance(value, str):
            raise TypeError("Asset type selection does not contain a valid value.")
        return AssetType(value)

    def currency(self) -> Currency:
        """Return the enum represented by the selected item data."""
        value = self._currency_combo.currentData()
        if not isinstance(value, str):
            raise TypeError("Currency selection does not contain a valid value.")
        return Currency(value)


__all__ = ["CreateAssetDialog"]
