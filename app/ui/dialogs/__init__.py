"""Focused desktop input dialogs."""

from app.ui.dialogs.create_asset_dialog import CreateAssetDialog
from app.ui.dialogs.create_portfolio_dialog import CreatePortfolioDialog
from app.ui.dialogs.record_market_price_dialog import RecordMarketPriceDialog
from app.ui.dialogs.record_trade_dialog import RecordTradeDialog
from app.ui.dialogs.settings_recovery_dialog import SettingsRecoveryDialog

__all__ = [
    "CreateAssetDialog",
    "CreatePortfolioDialog",
    "RecordMarketPriceDialog",
    "RecordTradeDialog",
    "SettingsRecoveryDialog",
]
