"""Desktop Alpha portfolio and Asset management window."""

from decimal import InvalidOperation
from uuid import UUID

from loguru import logger
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.application.commands import (
    BuyAssetCommand,
    CreateAssetCommand,
    CreatePortfolioCommand,
    DeleteTransactionCommand,
    RecordMarketPriceCommand,
    SellAssetCommand,
)
from app.application.exceptions import ApplicationError
from app.application.queries import GetPortfolioQuery, ListAssetsQuery, ListPortfoliosQuery
from app.application.results import (
    AssetPositionView,
    AssetView,
    PortfolioDetails,
    PortfolioSummary,
    TransactionView,
)
from app.application.services import (
    AssetApplicationService,
    MarketPriceApplicationService,
    PortfolioApplicationService,
)
from app.core.container import Container
from app.core.exceptions import RepositoryError
from app.domain.entities.asset import AssetType
from app.domain.entities.transaction import TransactionType
from app.domain.exceptions import DomainError
from app.ui.components.widgets import (
    CardWidget,
    ModernTable,
    PrimaryButton,
    SecondaryButton,
    SectionTitle,
)
from app.ui.dialogs import (
    CreateAssetDialog,
    CreatePortfolioDialog,
    RecordMarketPriceDialog,
    RecordTradeDialog,
)
from app.ui.theme.tokens import Colors, Spacing, Typography

_ASSET_TYPE_LABELS = {
    AssetType.EQUITY: "Equity",
    AssetType.FUND: "Fund",
    AssetType.ETF: "ETF",
    AssetType.BOND: "Bond",
    AssetType.CRYPTO: "Crypto",
    AssetType.CASH: "Cash",
}


class MainWindow(QMainWindow):
    """Present truthful setup workflows through application service contracts."""

    def __init__(self, container: Container) -> None:
        super().__init__()
        self._portfolio_service: PortfolioApplicationService = (
            container.portfolio_application_service
        )
        self._asset_service: AssetApplicationService = container.asset_application_service
        self._market_price_service: MarketPriceApplicationService = (
            container.market_price_application_service
        )
        self._portfolios: tuple[PortfolioSummary, ...] = ()
        self._assets: tuple[AssetView, ...] = ()
        self._selected_portfolio_id: UUID | None = None
        self._selected_portfolio_details: PortfolioDetails | None = None
        self._selected_asset_id: UUID | None = None
        self._selected_transaction_id: UUID | None = None

        self.setWindowTitle(container.settings.app_name)
        self.resize(1360, 860)
        self.setMinimumSize(1080, 700)
        self._build_toolbar()
        self._build_content()

        portfolios_loaded = self._refresh_portfolios()
        assets_loaded = self._refresh_assets()
        details_ready = (
            self._selected_portfolio_id is None or self._selected_portfolio_details is not None
        )
        if portfolios_loaded and assets_loaded and details_ready:
            self.statusBar().showMessage("Ready")

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Portfolio setup actions", self)
        toolbar.setObjectName("managementToolbar")
        toolbar.setMovable(False)

        brand = QLabel("MIRA", toolbar)
        brand.setStyleSheet(
            f"font-size: {Typography.HEADING}px; font-weight: 800; "
            f"color: {Colors.ACCENT}; letter-spacing: 2px;"
        )
        toolbar.addWidget(brand)
        toolbar.addSeparator()

        selector_label = QLabel("Portfolio", toolbar)
        selector_label.setStyleSheet(f"color: {Colors.MUTED};")
        toolbar.addWidget(selector_label)
        self._portfolio_selector = QComboBox(toolbar)
        self._portfolio_selector.setObjectName("portfolioSelector")
        self._portfolio_selector.setMinimumWidth(220)
        self._portfolio_selector.currentIndexChanged.connect(self._on_portfolio_selection_changed)
        toolbar.addWidget(self._portfolio_selector)

        spacer = QWidget(toolbar)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        self._new_portfolio_button = SecondaryButton("New portfolio", toolbar)
        self._new_portfolio_button.setObjectName("newPortfolioButton")
        self._new_portfolio_button.setStyleSheet(
            f"background: {Colors.SURFACE_RAISED}; border: 1px solid {Colors.BORDER};"
        )
        self._new_portfolio_button.clicked.connect(self._create_portfolio)
        toolbar.addWidget(self._new_portfolio_button)

        self._add_asset_button = PrimaryButton("Add asset", toolbar)
        self._add_asset_button.setObjectName("addAssetButton")
        self._add_asset_button.setStyleSheet(f"background: {Colors.ACCENT};")
        self._add_asset_button.clicked.connect(self._create_asset)
        toolbar.addWidget(self._add_asset_button)
        self.addToolBar(toolbar)

    def _build_content(self) -> None:
        central = QWidget(self)
        central.setObjectName("central")
        root = QHBoxLayout(central)
        root.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.LG, Spacing.LG)
        root.setSpacing(Spacing.LG)
        root.addWidget(self._navigation())
        root.addWidget(self._setup_workspace(), 1)
        self.setCentralWidget(central)

    def _navigation(self) -> QFrame:
        rail = QFrame(self)
        rail.setFixedWidth(196)
        rail.setStyleSheet(
            f"background: {Colors.SURFACE}; border: 1px solid {Colors.BORDER}; border-radius: 18px;"
        )
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(12, 18, 12, 18)
        layout.setSpacing(8)
        for name, active in (
            ("Setup & transactions", True),
            ("Valuation — coming later", False),
            ("Reports — coming later", False),
        ):
            item = QLabel(name, rail)
            item.setWordWrap(True)
            item.setMinimumHeight(38)
            item.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            item.setStyleSheet(
                "padding-left: 12px; border-radius: 8px; "
                f"font-weight: {'700' if active else '500'}; "
                f"background: {'#272250' if active else 'transparent'}; "
                f"color: {Colors.TEXT if active else Colors.MUTED};"
            )
            layout.addWidget(item)
        layout.addStretch()
        alpha_label = QLabel("DESKTOP ALPHA", rail)
        alpha_label.setStyleSheet(
            f"color: {Colors.MUTED}; font-size: 11px; font-weight: 700; padding-left: 12px;"
        )
        layout.addWidget(alpha_label)
        return rail

    def _setup_workspace(self) -> QWidget:
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.LG)

        title = QLabel("Portfolio setup", content)
        title.setStyleSheet(f"font-size: {Typography.DISPLAY}px; font-weight: 750;")
        subtitle = QLabel(
            "Create a portfolio and maintain the reusable Asset registry.",
            content,
        )
        subtitle.setStyleSheet(f"color: {Colors.MUTED};")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self._portfolio_card())
        workflow_splitter = QSplitter(Qt.Orientation.Vertical, content)
        workflow_splitter.setObjectName("workflowSplitter")
        workflow_splitter.addWidget(self._asset_registry_card())
        workflow_splitter.addWidget(self._transaction_card())
        workflow_splitter.setStretchFactor(0, 2)
        workflow_splitter.setStretchFactor(1, 3)
        layout.addWidget(workflow_splitter, 1)
        return content

    def _portfolio_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.SM)
        layout.addWidget(SectionTitle("Selected portfolio", card))

        self._portfolio_name_label = QLabel("No portfolio selected", card)
        self._portfolio_name_label.setObjectName("selectedPortfolioName")
        self._portfolio_name_label.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(self._portfolio_name_label)

        self._portfolio_details_label = QLabel(
            "Create or select a portfolio to begin.",
            card,
        )
        self._portfolio_details_label.setObjectName("selectedPortfolioDetails")
        self._portfolio_details_label.setStyleSheet(f"color: {Colors.MUTED};")
        layout.addWidget(self._portfolio_details_label)

        self._portfolio_empty_label = QLabel(
            "No portfolio selected. Create a portfolio to begin.",
            card,
        )
        self._portfolio_empty_label.setObjectName("portfolioEmptyState")
        self._portfolio_empty_label.setStyleSheet(f"color: {Colors.WARNING};")
        layout.addWidget(self._portfolio_empty_label)
        return card

    def _asset_registry_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.SM)
        header = QHBoxLayout()
        header.addWidget(SectionTitle("Asset registry", card))
        header.addStretch()
        self._record_price_button = SecondaryButton("Record price", card)
        self._record_price_button.setObjectName("recordPriceButton")
        self._record_price_button.setStyleSheet(
            f"background: {Colors.SURFACE_RAISED}; border: 1px solid {Colors.BORDER};"
        )
        self._record_price_button.clicked.connect(self._record_market_price)
        header.addWidget(self._record_price_button)
        layout.addLayout(header)

        explanation = QLabel(
            "Assets are reusable instruments. They become part of a portfolio "
            "when a transaction is recorded.",
            card,
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet(f"color: {Colors.MUTED};")
        layout.addWidget(explanation)

        self._asset_empty_label = QLabel(
            "No assets have been created yet.",
            card,
        )
        self._asset_empty_label.setObjectName("assetEmptyState")
        self._asset_empty_label.setStyleSheet(f"color: {Colors.WARNING};")
        layout.addWidget(self._asset_empty_label)

        self._asset_table = ModernTable(
            ["Symbol", "Name", "Type", "Currency", "Status"],
            card,
        )
        self._asset_table.setObjectName("assetRegistryTable")
        self._asset_table.setMinimumHeight(140)
        self._asset_table.itemSelectionChanged.connect(self._on_asset_selection_changed)
        layout.addWidget(self._asset_table, 1)
        return card

    def _transaction_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.SM)

        header = QHBoxLayout()
        header.addWidget(SectionTitle("Transaction history", card))
        header.addStretch()
        self._buy_asset_button = PrimaryButton("Buy", card)
        self._buy_asset_button.setObjectName("buyAssetButton")
        self._buy_asset_button.setStyleSheet(f"background: {Colors.ACCENT};")
        self._buy_asset_button.clicked.connect(self._buy_asset)
        header.addWidget(self._buy_asset_button)
        self._sell_asset_button = SecondaryButton("Sell", card)
        self._sell_asset_button.setObjectName("sellAssetButton")
        self._sell_asset_button.setStyleSheet(
            f"background: {Colors.SURFACE_RAISED}; border: 1px solid {Colors.BORDER};"
        )
        self._sell_asset_button.clicked.connect(self._sell_asset)
        header.addWidget(self._sell_asset_button)
        self._delete_transaction_button = SecondaryButton("Delete transaction", card)
        self._delete_transaction_button.setObjectName("deleteTransactionButton")
        self._delete_transaction_button.setStyleSheet(
            f"background: {Colors.SURFACE_RAISED}; border: 1px solid {Colors.BORDER};"
        )
        self._delete_transaction_button.clicked.connect(self._delete_selected_transaction)
        header.addWidget(self._delete_transaction_button)
        layout.addLayout(header)

        self._transaction_empty_label = QLabel(
            "Create or select a portfolio to record transactions.",
            card,
        )
        self._transaction_empty_label.setObjectName("transactionEmptyState")
        self._transaction_empty_label.setStyleSheet(f"color: {Colors.WARNING};")
        layout.addWidget(self._transaction_empty_label)

        self._transaction_table = ModernTable(
            [
                "Date",
                "Type",
                "Asset",
                "Quantity",
                "Unit price",
                "Currency",
                "Commission",
                "Tax",
            ],
            card,
        )
        self._transaction_table.setObjectName("transactionTable")
        self._transaction_table.setMinimumHeight(180)
        self._transaction_table.itemSelectionChanged.connect(self._on_transaction_selection_changed)
        layout.addWidget(self._transaction_table, 1)

        valuation_placeholder = QLabel(
            "Calculated valuation will be added in the next Alpha step.",
            card,
        )
        valuation_placeholder.setObjectName("futureWorkflowState")
        valuation_placeholder.setStyleSheet(f"color: {Colors.MUTED};")
        layout.addWidget(valuation_placeholder)
        return card

    def _refresh_portfolios(
        self,
        selected_portfolio_id: UUID | None = None,
    ) -> bool:
        selection_to_restore = (
            selected_portfolio_id
            if selected_portfolio_id is not None
            else self._selected_portfolio_id
        )
        try:
            portfolios = self._portfolio_service.list_portfolios(ListPortfoliosQuery())
        except (ApplicationError, DomainError, RepositoryError, ValueError, TypeError) as error:
            self._replace_portfolios(())
            self._portfolio_empty_label.setText("Unable to load portfolios.")
            self._show_error(
                "Unable to load portfolios",
                f"The portfolio list could not be loaded: {error}",
                "Unable to load portfolios",
            )
            return False
        except Exception:
            logger.exception("Unexpected failure while loading portfolios")
            self._replace_portfolios(())
            self._portfolio_empty_label.setText("Unable to load portfolios.")
            self._show_error(
                "Unable to load portfolios",
                "An unexpected error prevented the portfolio list from loading.",
                "Unable to load portfolios",
            )
            return False

        self._replace_portfolios(portfolios, selection_to_restore)
        return True

    def _replace_portfolios(
        self,
        portfolios: tuple[PortfolioSummary, ...],
        selected_portfolio_id: UUID | None = None,
    ) -> None:
        self._portfolios = portfolios
        blocker = QSignalBlocker(self._portfolio_selector)
        self._portfolio_selector.clear()
        for portfolio in portfolios:
            self._portfolio_selector.addItem(portfolio.name, str(portfolio.id))

        target_index = -1
        if portfolios:
            target_index = 0
            if selected_portfolio_id is not None:
                for index in range(self._portfolio_selector.count()):
                    if self._portfolio_selector.itemData(index) == str(selected_portfolio_id):
                        target_index = index
                        break
        self._portfolio_selector.setCurrentIndex(target_index)
        self._portfolio_selector.setEnabled(bool(portfolios))
        del blocker

        selected_id = self._portfolio_id_at(target_index)
        self._set_selected_portfolio(selected_id)

    def _refresh_assets(self, selected_asset_id: UUID | None = None) -> bool:
        selection_to_restore = (
            selected_asset_id if selected_asset_id is not None else self._selected_asset_id
        )
        try:
            assets = self._asset_service.list_assets(ListAssetsQuery())
        except (ApplicationError, DomainError, RepositoryError, ValueError, TypeError) as error:
            self._replace_assets(())
            self._asset_empty_label.setText("Unable to load assets.")
            self._show_error(
                "Unable to load assets",
                f"The Asset registry could not be loaded: {error}",
                "Unable to load assets",
            )
            return False
        except Exception:
            logger.exception("Unexpected failure while loading Assets")
            self._replace_assets(())
            self._asset_empty_label.setText("Unable to load assets.")
            self._show_error(
                "Unable to load assets",
                "An unexpected error prevented the Asset registry from loading.",
                "Unable to load assets",
            )
            return False

        self._replace_assets(assets, selection_to_restore)
        return True

    def _replace_assets(
        self,
        assets: tuple[AssetView, ...],
        selected_asset_id: UUID | None = None,
    ) -> None:
        self._assets = assets
        blocker = QSignalBlocker(self._asset_table)
        self._asset_table.clearContents()
        self._asset_table.setRowCount(0)
        target_row = -1
        for asset in assets:
            row = self._asset_table.add_row(
                (
                    asset.symbol,
                    asset.name,
                    _ASSET_TYPE_LABELS[asset.asset_type],
                    asset.currency.value,
                    "Active" if asset.is_active else "Inactive",
                )
            )
            symbol_item = self._asset_table.item(row, 0)
            if symbol_item is not None:
                symbol_item.setData(Qt.ItemDataRole.UserRole, str(asset.id))
            if asset.id == selected_asset_id:
                target_row = row

        self._asset_empty_label.setText("No assets have been created yet.")
        self._asset_empty_label.setVisible(not assets)
        self._selected_asset_id = None
        if target_row >= 0:
            self._asset_table.selectRow(target_row)
            self._selected_asset_id = selected_asset_id
        del blocker
        self._update_action_availability()

    def _on_portfolio_selection_changed(self, index: int) -> None:
        self._set_selected_portfolio(self._portfolio_id_at(index))

    def _portfolio_id_at(self, index: int) -> UUID | None:
        if index < 0:
            return None
        return self._uuid_from_data(self._portfolio_selector.itemData(index))

    def _set_selected_portfolio(self, portfolio_id: UUID | None) -> None:
        selected = next(
            (portfolio for portfolio in self._portfolios if portfolio.id == portfolio_id),
            None,
        )
        if selected is None:
            self._selected_portfolio_id = None
            self._selected_portfolio_details = None
            self._portfolio_name_label.setText("No portfolio selected")
            self._portfolio_details_label.setText("Create or select a portfolio to begin.")
            self._portfolio_empty_label.setText(
                "No portfolio selected. Create a portfolio to begin."
            )
            self._portfolio_empty_label.show()
            self._render_transactions()
            return

        identity_changed = selected.id != self._selected_portfolio_id
        self._selected_portfolio_id = selected.id
        state = "Archived" if selected.is_archived else "Active"
        created = selected.created_at.strftime("%Y-%m-%d %H:%M")
        self._portfolio_name_label.setText(selected.name)
        self._portfolio_details_label.setText(
            f"Base currency: {selected.base_currency.value}  •  "
            f"Created: {created}  •  Status: {state}"
        )
        self._portfolio_empty_label.hide()
        details_match_selection = (
            self._selected_portfolio_details is not None
            and self._selected_portfolio_details.id == selected.id
        )
        if identity_changed or not details_match_selection:
            self._selected_portfolio_details = None
            self._render_transactions()
            self._load_portfolio_details(selected.id)
        else:
            self._update_action_availability()

    def _on_asset_selection_changed(self) -> None:
        selected_items = self._asset_table.selectedItems()
        if not selected_items:
            self._selected_asset_id = None
            return
        symbol_item = self._asset_table.item(selected_items[0].row(), 0)
        self._selected_asset_id = (
            self._uuid_from_data(symbol_item.data(Qt.ItemDataRole.UserRole))
            if symbol_item is not None
            else None
        )

    def _load_portfolio_details(
        self,
        portfolio_id: UUID,
        selected_transaction_id: UUID | None = None,
    ) -> bool:
        try:
            details = self._portfolio_service.get_portfolio(
                GetPortfolioQuery(portfolio_id=portfolio_id)
            )
        except (
            ApplicationError,
            DomainError,
            RepositoryError,
            InvalidOperation,
            ValueError,
            TypeError,
        ) as error:
            if self._selected_portfolio_id == portfolio_id:
                self._selected_portfolio_details = None
                self._render_transactions()
                self._transaction_empty_label.setText("Unable to load Portfolio details.")
            self._show_error(
                "Unable to load Portfolio details",
                f"The selected Portfolio could not be loaded: {error}",
                "Unable to load Portfolio details",
            )
            return False
        except Exception:
            logger.exception("Unexpected failure while loading Portfolio details")
            if self._selected_portfolio_id == portfolio_id:
                self._selected_portfolio_details = None
                self._render_transactions()
                self._transaction_empty_label.setText("Unable to load Portfolio details.")
            self._show_error(
                "Unable to load Portfolio details",
                "An unexpected error prevented the selected Portfolio from loading.",
                "Unable to load Portfolio details",
            )
            return False

        if details.id != portfolio_id:
            logger.error(
                "Portfolio details identity mismatch: requested {}, received {}",
                portfolio_id,
                details.id,
            )
            if self._selected_portfolio_id == portfolio_id:
                self._selected_portfolio_details = None
                self._render_transactions()
            self._show_error(
                "Unable to load Portfolio details",
                "The selected Portfolio returned inconsistent details.",
                "Unable to load Portfolio details",
            )
            return False
        if self._selected_portfolio_id != portfolio_id:
            return False

        self._apply_portfolio_details(details, selected_transaction_id)
        return True

    def _apply_portfolio_details(
        self,
        details: PortfolioDetails,
        selected_transaction_id: UUID | None = None,
    ) -> None:
        if details.id != self._selected_portfolio_id:
            logger.error(
                "Ignored Portfolio details {} for selected Portfolio {}",
                details.id,
                self._selected_portfolio_id,
            )
            return
        self._selected_portfolio_details = details
        self._render_transactions(selected_transaction_id)

    def _render_transactions(
        self,
        selected_transaction_id: UUID | None = None,
    ) -> None:
        blocker = QSignalBlocker(self._transaction_table)
        self._transaction_table.clearContents()
        self._transaction_table.setRowCount(0)
        self._selected_transaction_id = None

        details = self._selected_portfolio_details
        if details is None:
            self._transaction_empty_label.setText(
                "Create or select a portfolio to record transactions."
                if self._selected_portfolio_id is None
                else "Portfolio transaction history is unavailable."
            )
            self._transaction_empty_label.show()
            del blocker
            self._update_action_availability()
            return

        portfolio_assets = {asset.id: asset for asset in details.assets}
        global_assets = {asset.id: asset for asset in self._assets}
        target_row = -1
        for transaction in details.transactions:
            asset = portfolio_assets.get(transaction.asset_id)
            if asset is None:
                logger.warning(
                    "Transaction {} references missing Portfolio Asset {}",
                    transaction.id,
                    transaction.asset_id,
                )
                fallback_asset = global_assets.get(transaction.asset_id)
                asset_label = (
                    self._asset_display_label(fallback_asset)
                    if fallback_asset is not None
                    else str(transaction.asset_id)
                )
                currency_label = (
                    fallback_asset.currency.value if fallback_asset is not None else "—"
                )
            else:
                asset_label = self._asset_display_label(asset)
                currency_label = asset.currency.value

            transaction_type = self._transaction_type_label(transaction.transaction_type)
            row = self._transaction_table.add_row(
                (
                    transaction.date.strftime("%Y-%m-%d %H:%M:%S"),
                    transaction_type,
                    asset_label,
                    str(transaction.quantity),
                    str(transaction.price),
                    currency_label,
                    str(transaction.commission),
                    str(transaction.tax),
                )
            )
            date_item = self._transaction_table.item(row, 0)
            if date_item is not None:
                date_item.setData(Qt.ItemDataRole.UserRole, str(transaction.id))
            if transaction.id == selected_transaction_id:
                target_row = row

        if details.transactions:
            self._transaction_empty_label.hide()
        else:
            self._transaction_empty_label.setText(
                "No transactions have been recorded for this portfolio."
            )
            self._transaction_empty_label.show()

        if target_row >= 0:
            self._transaction_table.selectRow(target_row)
            self._selected_transaction_id = selected_transaction_id
        del blocker
        self._update_action_availability()

    def _on_transaction_selection_changed(self) -> None:
        selected_items = self._transaction_table.selectedItems()
        if not selected_items:
            self._selected_transaction_id = None
        else:
            date_item = self._transaction_table.item(selected_items[0].row(), 0)
            self._selected_transaction_id = (
                self._uuid_from_data(date_item.data(Qt.ItemDataRole.UserRole))
                if date_item is not None
                else None
            )
        self._update_action_availability()

    def _update_action_availability(self) -> None:
        has_portfolio = self._selected_portfolio_id is not None
        has_global_assets = bool(self._assets)
        has_portfolio_assets = self._selected_portfolio_details is not None and bool(
            self._selected_portfolio_details.assets
        )
        self._buy_asset_button.setEnabled(has_portfolio and has_global_assets)
        self._sell_asset_button.setEnabled(has_portfolio and has_portfolio_assets)
        self._record_price_button.setEnabled(has_global_assets)
        self._delete_transaction_button.setEnabled(
            has_portfolio and self._selected_transaction_id is not None
        )

    @staticmethod
    def _asset_display_label(asset: AssetView | AssetPositionView) -> str:
        return f"{asset.symbol} — {asset.name}"

    @staticmethod
    def _transaction_type_label(transaction_type: TransactionType) -> str:
        if transaction_type is TransactionType.BUY:
            return "Buy"
        if transaction_type is TransactionType.SELL:
            return "Sell"
        return "Other"

    def _buy_asset(self) -> None:
        portfolio_id = self._selected_portfolio_id
        if portfolio_id is None or not self._assets:
            return
        dialog = RecordTradeDialog(TransactionType.BUY, self._assets, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            command = BuyAssetCommand(
                portfolio_id=portfolio_id,
                asset_id=dialog.asset_id(),
                quantity=dialog.quantity(),
                unit_price=dialog.unit_price(),
                trade_datetime=dialog.trade_datetime(),
                commission=dialog.commission(),
                tax=dialog.tax(),
            )
        except (InvalidOperation, TypeError, ValueError) as error:
            self._show_error(
                "Unable to record transaction",
                str(error),
                "Unable to record transaction",
            )
            return
        try:
            transaction = self._portfolio_service.buy_asset(command)
        except (
            ApplicationError,
            DomainError,
            RepositoryError,
            InvalidOperation,
            ValueError,
            TypeError,
        ) as error:
            self._show_error(
                "Unable to record transaction",
                f"The buy transaction could not be recorded: {error}",
                "Unable to record transaction",
            )
            return
        except Exception:
            logger.exception("Unexpected failure while recording a buy transaction")
            self._show_error(
                "Unable to record transaction",
                "An unexpected error prevented the buy transaction from being recorded.",
                "Unable to record transaction",
            )
            return

        if self._load_portfolio_details(portfolio_id, transaction.id):
            self.statusBar().showMessage("Buy transaction recorded")
        else:
            self.statusBar().showMessage(
                "Buy recorded, but transaction history could not be refreshed"
            )

    def _sell_asset(self) -> None:
        portfolio_id = self._selected_portfolio_id
        details = self._selected_portfolio_details
        if portfolio_id is None or details is None or not details.assets:
            return
        dialog = RecordTradeDialog(TransactionType.SELL, details.assets, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            command = SellAssetCommand(
                portfolio_id=portfolio_id,
                asset_id=dialog.asset_id(),
                quantity=dialog.quantity(),
                unit_price=dialog.unit_price(),
                trade_datetime=dialog.trade_datetime(),
                commission=dialog.commission(),
                tax=dialog.tax(),
            )
        except (InvalidOperation, TypeError, ValueError) as error:
            self._show_error(
                "Unable to record transaction",
                str(error),
                "Unable to record transaction",
            )
            return
        try:
            transaction = self._portfolio_service.sell_asset(command)
        except (
            ApplicationError,
            DomainError,
            RepositoryError,
            InvalidOperation,
            ValueError,
            TypeError,
        ) as error:
            self._show_error(
                "Unable to record transaction",
                f"The sell transaction could not be recorded: {error}",
                "Unable to record transaction",
            )
            return
        except Exception:
            logger.exception("Unexpected failure while recording a sell transaction")
            self._show_error(
                "Unable to record transaction",
                "An unexpected error prevented the sell transaction from being recorded.",
                "Unable to record transaction",
            )
            return

        if self._load_portfolio_details(portfolio_id, transaction.id):
            self.statusBar().showMessage("Sell transaction recorded")
        else:
            self.statusBar().showMessage(
                "Sell recorded, but transaction history could not be refreshed"
            )

    def _delete_selected_transaction(self) -> None:
        portfolio_id = self._selected_portfolio_id
        transaction_id = self._selected_transaction_id
        details = self._selected_portfolio_details
        if portfolio_id is None or transaction_id is None or details is None:
            return
        transaction = next(
            (candidate for candidate in details.transactions if candidate.id == transaction_id),
            None,
        )
        if transaction is None:
            self._selected_transaction_id = None
            self._update_action_availability()
            return

        description = self._transaction_description(transaction, details)
        answer = QMessageBox.question(
            self,
            "Delete transaction",
            f"Delete this transaction?\n\n{description}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        command = DeleteTransactionCommand(
            portfolio_id=portfolio_id,
            transaction_id=transaction_id,
        )
        try:
            updated = self._portfolio_service.delete_transaction(command)
        except (
            ApplicationError,
            DomainError,
            RepositoryError,
            InvalidOperation,
            ValueError,
            TypeError,
        ) as error:
            self._show_error(
                "Unable to delete transaction",
                f"The transaction could not be deleted: {error}",
                "Unable to delete transaction",
            )
            return
        except Exception:
            logger.exception("Unexpected failure while deleting a transaction")
            self._show_error(
                "Unable to delete transaction",
                "An unexpected error prevented the transaction from being deleted.",
                "Unable to delete transaction",
            )
            return

        self._apply_portfolio_details(updated)
        self.statusBar().showMessage("Transaction deleted")

    def _transaction_description(
        self,
        transaction: TransactionView,
        details: PortfolioDetails,
    ) -> str:
        asset = next(
            (candidate for candidate in details.assets if candidate.id == transaction.asset_id),
            None,
        )
        asset_label = (
            self._asset_display_label(asset) if asset is not None else str(transaction.asset_id)
        )
        transaction_type = self._transaction_type_label(transaction.transaction_type)
        return (
            f"{transaction_type} · {asset_label} · {transaction.date.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _record_market_price(self) -> None:
        if not self._assets:
            return
        dialog = RecordMarketPriceDialog(self._assets, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            command = RecordMarketPriceCommand(
                asset_id=dialog.asset_id(),
                price=dialog.price(),
                observed_at=dialog.observed_at(),
            )
        except (InvalidOperation, TypeError, ValueError) as error:
            self._show_error(
                "Unable to record price",
                str(error),
                "Unable to record price",
            )
            return
        try:
            recorded = self._market_price_service.record_market_price(command)
        except (
            ApplicationError,
            DomainError,
            RepositoryError,
            InvalidOperation,
            ValueError,
            TypeError,
        ) as error:
            self._show_error(
                "Unable to record price",
                f"The market price could not be recorded: {error}",
                "Unable to record price",
            )
            return
        except Exception:
            logger.exception("Unexpected failure while recording a market price")
            self._show_error(
                "Unable to record price",
                "An unexpected error prevented the market price from being recorded.",
                "Unable to record price",
            )
            return

        asset = next(
            (candidate for candidate in self._assets if candidate.id == recorded.asset_id),
            None,
        )
        asset_label = asset.symbol if asset is not None else "Asset"
        self.statusBar().showMessage(
            f"Price recorded for {asset_label}: {recorded.price} {recorded.currency.value}"
        )

    def _create_portfolio(self) -> None:
        dialog = CreatePortfolioDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        command = CreatePortfolioCommand(portfolio_name=dialog.portfolio_name())
        try:
            created = self._portfolio_service.create_portfolio(command)
        except (ApplicationError, DomainError, RepositoryError, ValueError, TypeError) as error:
            self._show_error(
                "Unable to create portfolio",
                f"The portfolio could not be created: {error}",
                "Unable to create portfolio",
            )
            return
        except Exception:
            logger.exception("Unexpected failure while creating a portfolio")
            self._show_error(
                "Unable to create portfolio",
                "An unexpected error prevented the portfolio from being created.",
                "Unable to create portfolio",
            )
            return

        portfolios_refreshed = self._refresh_portfolios(created.id)
        details_loaded = (
            self._selected_portfolio_id == created.id
            and self._selected_portfolio_details is not None
        )
        if portfolios_refreshed and details_loaded:
            self.statusBar().showMessage("Portfolio created")
        elif portfolios_refreshed:
            self.statusBar().showMessage("Portfolio created, but its details could not be loaded")
        else:
            self.statusBar().showMessage("Portfolio created, but the list could not be refreshed")

    def _create_asset(self) -> None:
        dialog = CreateAssetDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        command = CreateAssetCommand(
            symbol=dialog.symbol(),
            name=dialog.asset_name(),
            asset_type=dialog.asset_type(),
            currency=dialog.currency(),
        )
        try:
            created = self._asset_service.create_asset(command)
        except (ApplicationError, DomainError, RepositoryError, ValueError, TypeError) as error:
            self._show_error(
                "Unable to create asset",
                f"The Asset could not be created: {error}",
                "Unable to create asset",
            )
            return
        except Exception:
            logger.exception("Unexpected failure while creating an Asset")
            self._show_error(
                "Unable to create asset",
                "An unexpected error prevented the Asset from being created.",
                "Unable to create asset",
            )
            return

        if self._refresh_assets(created.id):
            self.statusBar().showMessage("Asset created")
        else:
            self.statusBar().showMessage("Asset created, but the list could not be refreshed")

    def _show_error(self, title: str, message: str, status: str) -> None:
        self.statusBar().showMessage(status)
        QMessageBox.critical(self, title, message)

    @staticmethod
    def _uuid_from_data(value: object) -> UUID | None:
        if not isinstance(value, str):
            return None
        try:
            return UUID(value)
        except ValueError:
            return None


__all__ = ["MainWindow"]
