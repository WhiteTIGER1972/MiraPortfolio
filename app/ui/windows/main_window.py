"""Main dashboard window."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.core.container import Container
from app.ui.components.widgets import (
    CardWidget,
    MetricCard,
    ModernTable,
    PrimaryButton,
    SecondaryButton,
    SectionTitle,
)
from app.ui.theme.tokens import Colors, Spacing, Typography


class MainWindow(QMainWindow):
    """Premium portfolio dashboard shell."""

    def __init__(self, container: Container) -> None:
        super().__init__()
        self._container = container
        self.setWindowTitle(container.settings.app_name)
        self.resize(1360, 860)
        self.setMinimumSize(1080, 700)
        self._build_toolbar()
        self._build_content()
        self.statusBar().showMessage("Market data refreshes automatically during trading hours")

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main navigation", self)
        toolbar.setMovable(False)
        brand = QLabel("MIRA")
        brand.setStyleSheet(
            f"font-size: {Typography.HEADING}px; font-weight: 800; "
            f"color: {Colors.ACCENT}; letter-spacing: 2px;"
        )
        toolbar.addWidget(brand)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("Portfolio overview"))
        toolbar.addWidget(QWidget())
        toolbar.addWidget(SecondaryButton("Search"))
        toolbar.addWidget(PrimaryButton("+ Add asset"))
        self.addToolBar(toolbar)

    def _build_content(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        root = QHBoxLayout(central)
        root.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.LG, Spacing.LG)
        root.setSpacing(Spacing.LG)
        root.addWidget(self._navigation())
        root.addWidget(self._dashboard(), 1)
        self.setCentralWidget(central)

    def _navigation(self) -> QFrame:
        rail = QFrame()
        rail.setFixedWidth(196)
        rail.setStyleSheet(
            f"background: {Colors.SURFACE}; border: 1px solid {Colors.BORDER}; border-radius: 18px;"
        )
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(12, 18, 12, 18)
        layout.setSpacing(8)
        for name, active in [
            ("Overview", True),
            ("Portfolio", False),
            ("Markets", False),
            ("Watchlist", False),
            ("Reports", False),
        ]:
            item = QLabel(name)
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
        layout.addWidget(
            QLabel(
                "CONNECTED",
                styleSheet=(
                    f"color: {Colors.POSITIVE}; font-size: 11px; "
                    "font-weight: 700; padding-left: 12px;"
                ),
            )
        )
        return rail

    def _dashboard(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.LG)
        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Good afternoon, Mira")
        title.setStyleSheet(f"font-size: {Typography.DISPLAY}px; font-weight: 750;")
        subtitle = QLabel("Your investments are moving with the market.")
        subtitle.setStyleSheet(f"color: {Colors.MUTED};")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box)
        heading.addStretch()
        heading.addWidget(QLabel("Friday, 18 July", styleSheet=f"color: {Colors.MUTED};"))
        layout.addLayout(heading)
        metrics = QHBoxLayout()
        metrics.setSpacing(Spacing.MD)
        for metric in [
            ("Total value", "?1,284,650", "+?18,422  /  1.46%", True),
            ("Today’s P&L", "+?12,840", "+1.01%", True),
            ("Total return", "+?284,650", "+28.46%", True),
            ("Cash available", "?47,200", "3.7% allocated", True),
        ]:
            metrics.addWidget(MetricCard(*metric))
        layout.addLayout(metrics)
        allocation = CardWidget()
        allocation_layout = QVBoxLayout(allocation)
        allocation_layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        allocation_layout.addWidget(SectionTitle("Portfolio allocation"))
        allocation_layout.addWidget(
            QLabel(
                "Equities  52%     Funds  27%     Fixed income  13%     Cash  8%",
                styleSheet=f"color: {Colors.MUTED}; padding: 10px 0;",
            )
        )
        allocation_bar = QLabel()
        allocation_bar.setFixedHeight(12)
        allocation_bar.setStyleSheet(
            "border-radius: 6px; background: qlineargradient("
            "x1:0, x2:1, stop:0 #7C6CF2, stop:0.52 #4C9EFF, "
            "stop:0.79 #37D19A, stop:0.92 #FFB86B);"
        )
        allocation_layout.addWidget(allocation_bar)
        layout.addWidget(allocation)
        holdings = QHBoxLayout()
        table_card = CardWidget()
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        table_layout.addWidget(SectionTitle("Top holdings"))
        table = ModernTable(["Asset", "Value", "Today", "Allocation"])
        for row in [
            ("THYAO", "?214,820", "+2.14%", "16.7%"),
            ("TUPRS", "?168,400", "+1.32%", "13.1%"),
            ("AAPL", "?142,200", "+0.84%", "11.1%"),
        ]:
            table.add_row(row)
        table_layout.addWidget(table)
        holdings.addWidget(table_card, 3)
        insight = CardWidget()
        insight.setFixedWidth(280)
        insight_layout = QVBoxLayout(insight)
        insight_layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        insight_layout.addWidget(SectionTitle("Market pulse"))
        insight_layout.addWidget(
            QLabel("BIST 100", styleSheet=f"color: {Colors.MUTED}; margin-top: 12px;")
        )
        insight_layout.addWidget(
            QLabel("10,452.18", styleSheet="font-size: 24px; font-weight: 700;")
        )
        insight_layout.addWidget(
            QLabel("+0.72% today", styleSheet=f"color: {Colors.POSITIVE}; font-weight: 700;")
        )
        insight_layout.addStretch()
        insight_layout.addWidget(SecondaryButton("View markets"))
        holdings.addWidget(insight)
        layout.addLayout(holdings, 1)
        return content
