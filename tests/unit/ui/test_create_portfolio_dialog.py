"""Contract tests for the focused Portfolio creation dialog."""

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton, QWidget

from app.ui.dialogs.create_portfolio_dialog import CreatePortfolioDialog


def test_portfolio_dialog_exposes_exact_input_and_accepts_with_enter(
    qapplication: QApplication,
) -> None:
    parent = QWidget()
    dialog = CreatePortfolioDialog(parent)
    try:
        inputs = dialog.findChildren(QLineEdit)
        assert len(inputs) == 1
        assert inputs[0].objectName() == "portfolioNameInput"
        assert dialog.parent() is parent
        assert dialog.isModal()
        assert dialog.minimumWidth() >= 400

        inputs[0].setText("  Growth & Income  ")
        assert dialog.portfolio_name() == "  Growth & Income  "
        dialog.show()
        inputs[0].setFocus()
        QTest.keyClick(inputs[0], Qt.Key.Key_Return)

        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.close()
        parent.close()
        dialog.deleteLater()
        parent.deleteLater()
        qapplication.processEvents()


def test_portfolio_dialog_has_create_and_cancel_controls(
    qapplication: QApplication,
) -> None:
    dialog = CreatePortfolioDialog()
    try:
        create_button = dialog.findChild(QPushButton, "createPortfolioDialogButton")
        cancel_button = dialog.findChild(QPushButton, "cancelPortfolioDialogButton")

        assert create_button is not None
        assert create_button.text() == "Create"
        assert create_button.isDefault()
        assert cancel_button is not None
        assert cancel_button.text() == "Cancel"

        QTest.mouseClick(cancel_button, Qt.MouseButton.LeftButton)
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.close()
        dialog.deleteLater()
        qapplication.processEvents()
