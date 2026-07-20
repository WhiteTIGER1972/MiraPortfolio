"""Contract tests for the focused Asset creation dialog."""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLineEdit, QPushButton

from app.domain.entities.asset import AssetType
from app.domain.value_objects.currency import Currency
from app.ui.dialogs.create_asset_dialog import CreateAssetDialog


def test_asset_dialog_fields_defaults_and_exact_enum_mappings(
    qapplication: QApplication,
) -> None:
    dialog = CreateAssetDialog()
    try:
        symbol_input = dialog.findChild(QLineEdit, "assetSymbolInput")
        name_input = dialog.findChild(QLineEdit, "assetNameInput")
        type_input = dialog.findChild(QComboBox, "assetTypeInput")
        currency_input = dialog.findChild(QComboBox, "assetCurrencyInput")

        assert symbol_input is not None
        assert name_input is not None
        assert type_input is not None
        assert currency_input is not None
        assert type_input.count() == 6
        assert [type_input.itemText(index) for index in range(type_input.count())] == [
            "Equity",
            "Fund",
            "ETF",
            "Bond",
            "Crypto",
            "Cash",
        ]
        assert currency_input.count() == 4
        assert [currency_input.itemText(index) for index in range(currency_input.count())] == [
            "TRY",
            "USD",
            "EUR",
            "GBP",
        ]
        assert dialog.asset_type() is AssetType.EQUITY
        assert dialog.currency() is Currency.TRY

        for index, expected in enumerate(AssetType):
            type_input.setCurrentIndex(index)
            assert dialog.asset_type() is expected
            assert type_input.itemData(index) == expected.value
        for index, expected in enumerate(Currency):
            currency_input.setCurrentIndex(index)
            assert dialog.currency() is expected
            assert currency_input.itemData(index) == expected.value

        symbol_input.setText("  msft  ")
        name_input.setText("  Microsoft  ")
        assert dialog.symbol() == "  msft  "
        assert dialog.asset_name() == "  Microsoft  "
    finally:
        dialog.close()
        dialog.deleteLater()
        qapplication.processEvents()


def test_asset_dialog_accepts_and_rejects_with_standard_controls(
    qapplication: QApplication,
) -> None:
    accepted_dialog = CreateAssetDialog()
    rejected_dialog = CreateAssetDialog()
    try:
        create_button = accepted_dialog.findChild(QPushButton, "createAssetDialogButton")
        cancel_button = rejected_dialog.findChild(QPushButton, "cancelAssetDialogButton")
        assert create_button is not None
        assert create_button.text() == "Create"
        assert create_button.isDefault()
        assert cancel_button is not None
        assert cancel_button.text() == "Cancel"

        QTest.mouseClick(create_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(cancel_button, Qt.MouseButton.LeftButton)

        assert accepted_dialog.result() == QDialog.DialogCode.Accepted
        assert rejected_dialog.result() == QDialog.DialogCode.Rejected
    finally:
        accepted_dialog.close()
        rejected_dialog.close()
        accepted_dialog.deleteLater()
        rejected_dialog.deleteLater()
        qapplication.processEvents()
