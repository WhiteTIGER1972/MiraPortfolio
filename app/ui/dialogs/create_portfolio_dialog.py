"""Portfolio creation input dialog."""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)


class CreatePortfolioDialog(QDialog):
    """Collect the name required by the Portfolio creation use case."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create portfolio")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._name_input = QLineEdit(self)
        self._name_input.setObjectName("portfolioNameInput")
        self._name_input.setPlaceholderText("Long-term investments")
        form.addRow("Portfolio name", self._name_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.setObjectName("portfolioDialogButtons")
        create_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        create_button.setText("Create")
        create_button.setObjectName("createPortfolioDialogButton")
        create_button.setDefault(True)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setObjectName("cancelPortfolioDialogButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._name_input.setFocus()

    def portfolio_name(self) -> str:
        """Return the entered name without changing application semantics."""
        return self._name_input.text()


__all__ = ["CreatePortfolioDialog"]
