"""Settings, backup, restore, and support workflows over application contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Final, override

from PySide6.QtCore import QObject, QSignalBlocker, Qt, QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.application.backup import (
    BackupKind,
    BackupListing,
    BackupRecord,
    BackupService,
)
from app.application.diagnostics import DiagnosticsService, SupportBundleRecord
from app.application.preferences import (
    LogLevelPreference,
    PreferenceField,
    PreferenceSnapshot,
    PreferencesService,
    UserPreferences,
)
from app.application.restore import RestoreService, RestoreStageResult
from app.core.exceptions import BackupError, DiagnosticsError, PreferencesError, RestoreError
from app.ui.components.widgets import (
    CardWidget,
    ModernTable,
    PrimaryButton,
    SecondaryButton,
    SectionTitle,
)
from app.ui.theme.tokens import Colors, Spacing, Typography

_LOAD_RECOVERY: Final = "load_recovery"
_REFRESH_BACKUPS: Final = "refresh_backups"
_CREATE_BACKUP: Final = "create_backup"
_VERIFY_SELECTED: Final = "verify_selected"
_VERIFY_EXTERNAL: Final = "verify_external"
_VERIFY_EXTERNAL_FOR_RESTORE: Final = "verify_external_for_restore"
_STAGE_RESTORE: Final = "stage_restore"
_CANCEL_RESTORE: Final = "cancel_restore"
_CREATE_SUPPORT: Final = "create_support"

_BACKUP_FILTER: Final = "Mira backup (*.mirabackup);;All files (*)"
_RESTORE_CONFIRMATION: Final = (
    "The current database will not change now.\n\n"
    "The restore will be applied at the next application start. A pre-restore "
    "safety backup will be created during startup. Close the application and "
    "start it again yourself to complete the restore."
)


@dataclass(frozen=True, slots=True)
class _RecoveryState:
    listing: BackupListing | None
    pending: RestoreStageResult | None
    backup_load_failed: bool
    restore_load_failed: bool


@dataclass(frozen=True, slots=True)
class _CreatedBackup:
    record: BackupRecord
    listing: BackupListing | None
    refresh_failed: bool


@dataclass(frozen=True, slots=True)
class _StagedRestore:
    result: RestoreStageResult
    pending: RestoreStageResult
    refresh_failed: bool


@dataclass(frozen=True, slots=True)
class _CancelledRestore:
    cancelled: bool
    pending: RestoreStageResult | None
    refresh_failed: bool


def _load_recovery_state(
    backup_service: BackupService,
    restore_service: RestoreService,
) -> _RecoveryState:
    try:
        listing = backup_service.list_backups()
        backup_load_failed = False
    except BackupError:
        listing = None
        backup_load_failed = True

    try:
        pending = restore_service.get_pending_restore()
        restore_load_failed = False
    except RestoreError:
        pending = None
        restore_load_failed = True

    return _RecoveryState(
        listing=listing,
        pending=pending,
        backup_load_failed=backup_load_failed,
        restore_load_failed=restore_load_failed,
    )


def _create_backup_and_refresh(backup_service: BackupService) -> _CreatedBackup:
    record = backup_service.create_backup(BackupKind.MANUAL)
    try:
        listing = backup_service.list_backups()
    except BackupError:
        return _CreatedBackup(record=record, listing=None, refresh_failed=True)
    return _CreatedBackup(record=record, listing=listing, refresh_failed=False)


def _stage_restore_and_refresh(
    restore_service: RestoreService,
    backup_path: Path,
) -> _StagedRestore:
    result = restore_service.stage_restore(backup_path)
    try:
        pending = restore_service.get_pending_restore()
    except RestoreError:
        return _StagedRestore(result=result, pending=result, refresh_failed=True)
    return _StagedRestore(
        result=result,
        pending=pending or result,
        refresh_failed=False,
    )


def _cancel_restore_and_refresh(restore_service: RestoreService) -> _CancelledRestore:
    cancelled = restore_service.cancel_pending_restore()
    try:
        pending = restore_service.get_pending_restore()
    except RestoreError:
        return _CancelledRestore(
            cancelled=cancelled,
            pending=None,
            refresh_failed=True,
        )
    return _CancelledRestore(
        cancelled=cancelled,
        pending=pending,
        refresh_failed=False,
    )


class _OperationWorker(QObject):
    """Run one bounded recovery operation on an owned Qt thread."""

    succeeded = Signal(str, object)
    expected_failure = Signal(str)
    finished = Signal(str)

    def __init__(
        self,
        name: str,
        operation: Callable[[], object],
        expected_errors: tuple[type[Exception], ...],
    ) -> None:
        super().__init__()
        self._name = name
        self._operation = operation
        self._expected_errors = expected_errors

    @Slot()
    def run(self) -> None:
        """Catch only declared operational failures; unexpected failures escape."""
        try:
            result = self._operation()
        except self._expected_errors:
            self.expected_failure.emit(self._name)
        else:
            self.succeeded.emit(self._name, result)
        finally:
            self.finished.emit(self._name)


class SettingsRecoveryDialog(QDialog):
    """Present user-controlled settings and recovery service operations.

    Unsaved log-level selections are never persisted implicitly. Closing asks the
    user whether to discard a dirty selection.
    """

    def __init__(
        self,
        preferences_service: PreferencesService,
        backup_service: BackupService,
        restore_service: RestoreService,
        diagnostics_service: DiagnosticsService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._preferences_service = preferences_service
        self._backup_service = backup_service
        self._restore_service = restore_service
        self._diagnostics_service = diagnostics_service
        self._preference_snapshot: PreferenceSnapshot | None = None
        self._preference_proposal: LogLevelPreference | None = None
        self._backups: tuple[BackupRecord, ...] = ()
        self._pending_restore: RestoreStageResult | None = None
        self._restore_status_available = False
        self._active_operation: str | None = None
        self._after_operation: Callable[[], None] | None = None
        self._live_threads: list[QThread] = []
        self._live_workers: list[_OperationWorker] = []

        self.setObjectName("settingsRecoveryDialog")
        self.setWindowTitle("Settings & recovery")
        self.setModal(True)
        self.resize(1040, 790)
        self.setMinimumSize(920, 680)
        self._build_ui()
        self._load_preferences()
        self._connect_shutdown_wait()
        self._start_operation(
            _LOAD_RECOVERY,
            partial(_load_recovery_state, backup_service, restore_service),
            (BackupError, RestoreError),
            "Loading backup and restore status…",
        )

    @property
    def operation_in_progress(self) -> bool:
        """Report whether a service operation is currently executing."""
        return self._active_operation is not None

    @property
    def running_worker_count(self) -> int:
        """Return the number of owned Qt threads that have not yet stopped."""
        return sum(thread.isRunning() for thread in self._live_threads)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        root.setSpacing(Spacing.MD)

        heading = QLabel("Settings & recovery", self)
        heading.setObjectName("settingsRecoveryTitle")
        heading.setStyleSheet(f"font-size: {Typography.DISPLAY}px; font-weight: 750;")
        explanation = QLabel(
            "Manage the effective log-level preference, verified backups, restart-safe "
            "restore requests, and privacy-safe support diagnostics.",
            self,
        )
        explanation.setObjectName("settingsRecoveryExplanation")
        explanation.setWordWrap(True)
        explanation.setStyleSheet(f"color: {Colors.MUTED};")
        root.addWidget(heading)
        root.addWidget(explanation)

        self._tabs = QTabWidget(self)
        self._tabs.setObjectName("settingsRecoveryTabs")
        self._tabs.addTab(self._build_preferences_tab(), "Preferences")
        self._tabs.addTab(self._build_recovery_tab(), "Backup & Restore")
        self._tabs.addTab(self._build_support_tab(), "Support")
        root.addWidget(self._tabs, 1)

        footer = QHBoxLayout()
        self._operation_status = QLabel("Ready", self)
        self._operation_status.setObjectName("settingsRecoveryStatus")
        self._operation_status.setWordWrap(True)
        self._operation_status.setStyleSheet(f"color: {Colors.MUTED};")
        footer.addWidget(self._operation_status, 1)
        self._close_button = SecondaryButton("Close", self)
        self._close_button.setObjectName("closeSettingsRecoveryButton")
        _style_secondary(self._close_button)
        self._close_button.clicked.connect(self.reject)
        footer.addWidget(self._close_button)
        root.addLayout(footer)

    def _build_preferences_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(Spacing.MD, Spacing.MD, Spacing.MD, Spacing.MD)
        layout.setSpacing(Spacing.MD)

        card = CardWidget(tab)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        card_layout.setSpacing(Spacing.MD)
        card_layout.addWidget(SectionTitle("Log level", card))

        description = QLabel(
            "Choose the logging threshold for the next application start. The running "
            "application is not reconfigured.",
            card,
        )
        description.setWordWrap(True)
        description.setStyleSheet(f"color: {Colors.MUTED};")
        card_layout.addWidget(description)

        form = QFormLayout()
        form.setSpacing(Spacing.SM)
        self._log_level_combo = QComboBox(card)
        self._log_level_combo.setObjectName("logLevelCombo")
        for level in LogLevelPreference:
            self._log_level_combo.addItem(level.value, level.value)
        self._log_level_combo.currentIndexChanged.connect(self._on_log_level_changed)
        form.addRow("Effective log level", self._log_level_combo)

        self._preference_load_status = _value_label("preferenceLoadStatus", card)
        form.addRow("Load status", self._preference_load_status)
        self._preference_format_version = _value_label("preferenceFormatVersion", card)
        form.addRow("Source format version", self._preference_format_version)
        self._preference_file_exists = _value_label("preferenceFileExists", card)
        form.addRow("Preference file exists", self._preference_file_exists)
        self._preference_environment = _value_label("preferenceEnvironmentOverride", card)
        self._preference_environment.setWordWrap(True)
        form.addRow("Environment ownership", self._preference_environment)
        self._preference_restart = _value_label("preferenceRestartRequired", card)
        self._preference_restart.setWordWrap(True)
        form.addRow("Restart status", self._preference_restart)
        card_layout.addLayout(form)

        self._preference_status = QLabel("", card)
        self._preference_status.setObjectName("preferenceStatus")
        self._preference_status.setWordWrap(True)
        self._preference_status.setStyleSheet(f"color: {Colors.MUTED};")
        card_layout.addWidget(self._preference_status)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._reset_preferences_button = SecondaryButton("Reset", card)
        self._reset_preferences_button.setObjectName("resetPreferencesButton")
        _style_secondary(self._reset_preferences_button)
        self._reset_preferences_button.clicked.connect(self._reset_preferences)
        actions.addWidget(self._reset_preferences_button)
        self._save_preferences_button = PrimaryButton("Save", card)
        self._save_preferences_button.setObjectName("savePreferencesButton")
        _style_primary(self._save_preferences_button)
        self._save_preferences_button.clicked.connect(self._save_preferences)
        actions.addWidget(self._save_preferences_button)
        card_layout.addLayout(actions)

        layout.addWidget(card)
        layout.addStretch(1)
        return tab

    def _build_recovery_tab(self) -> QWidget:
        scroll = QScrollArea(self)
        scroll.setObjectName("backupRestoreScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(Spacing.MD, Spacing.MD, Spacing.MD, Spacing.MD)
        layout.setSpacing(Spacing.MD)
        layout.addWidget(self._build_backup_card(content))
        layout.addWidget(self._build_restore_card(content))
        layout.addWidget(self._build_pending_restore_card(content))
        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll

    def _build_backup_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.SM)

        header = QHBoxLayout()
        header.addWidget(SectionTitle("Backup management", card))
        header.addStretch(1)
        self._refresh_backups_button = SecondaryButton("Refresh", card)
        self._refresh_backups_button.setObjectName("refreshBackupsButton")
        _style_secondary(self._refresh_backups_button)
        self._refresh_backups_button.clicked.connect(self._refresh_backups)
        header.addWidget(self._refresh_backups_button)
        self._create_backup_button = PrimaryButton("Create backup", card)
        self._create_backup_button.setObjectName("createBackupButton")
        _style_primary(self._create_backup_button)
        self._create_backup_button.clicked.connect(self._create_backup)
        header.addWidget(self._create_backup_button)
        layout.addLayout(header)

        description = QLabel(
            "Only backups verified by the backup service are listed. Creating a backup "
            "is always an explicit action.",
            card,
        )
        description.setWordWrap(True)
        description.setStyleSheet(f"color: {Colors.MUTED};")
        layout.addWidget(description)

        self._backup_empty_state = QLabel("Loading verified backups…", card)
        self._backup_empty_state.setObjectName("backupEmptyState")
        self._backup_empty_state.setStyleSheet(f"color: {Colors.MUTED};")
        layout.addWidget(self._backup_empty_state)

        self._backup_table = ModernTable(
            [
                "Created UTC",
                "Filename",
                "Kind",
                "Backup size",
                "Database size",
                "Revision",
            ],
            card,
        )
        self._backup_table.setObjectName("backupTable")
        self._backup_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._backup_table.setMinimumHeight(210)
        self._backup_table.itemSelectionChanged.connect(self._refresh_control_states)
        layout.addWidget(self._backup_table)

        self._invalid_backup_count = QLabel("Invalid backup entries: 0", card)
        self._invalid_backup_count.setObjectName("invalidBackupCount")
        self._invalid_backup_count.setStyleSheet(f"color: {Colors.MUTED};")
        layout.addWidget(self._invalid_backup_count)

        actions = QHBoxLayout()
        self._verify_selected_button = SecondaryButton("Verify selected", card)
        self._verify_selected_button.setObjectName("verifySelectedBackupButton")
        _style_secondary(self._verify_selected_button)
        self._verify_selected_button.clicked.connect(self._verify_selected_backup)
        actions.addWidget(self._verify_selected_button)
        self._verify_file_button = SecondaryButton("Verify backup file…", card)
        self._verify_file_button.setObjectName("verifyBackupFileButton")
        _style_secondary(self._verify_file_button)
        self._verify_file_button.clicked.connect(self._verify_external_backup)
        actions.addWidget(self._verify_file_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return card

    def _build_restore_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.SM)
        layout.addWidget(SectionTitle("Restore staging", card))

        description = QLabel(
            "Staging verifies and prepares a backup for the next application start. "
            "It does not change the current database and does not restart the application.",
            card,
        )
        description.setWordWrap(True)
        description.setStyleSheet(f"color: {Colors.MUTED};")
        layout.addWidget(description)

        actions = QHBoxLayout()
        self._stage_selected_button = PrimaryButton("Stage selected backup", card)
        self._stage_selected_button.setObjectName("stageSelectedRestoreButton")
        _style_primary(self._stage_selected_button)
        self._stage_selected_button.clicked.connect(self._stage_selected_restore)
        actions.addWidget(self._stage_selected_button)
        self._stage_file_button = SecondaryButton("Stage backup file…", card)
        self._stage_file_button.setObjectName("stageRestoreFileButton")
        _style_secondary(self._stage_file_button)
        self._stage_file_button.clicked.connect(self._stage_external_restore)
        actions.addWidget(self._stage_file_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return card

    def _build_pending_restore_card(self, parent: QWidget) -> CardWidget:
        card = CardWidget(parent)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        layout.setSpacing(Spacing.SM)
        layout.addWidget(SectionTitle("Pending restore", card))

        self._pending_restore_status = QLabel("Loading pending restore status…", card)
        self._pending_restore_status.setObjectName("pendingRestoreStatus")
        self._pending_restore_status.setWordWrap(True)
        layout.addWidget(self._pending_restore_status)
        self._pending_restore_details = QLabel("", card)
        self._pending_restore_details.setObjectName("pendingRestoreDetails")
        self._pending_restore_details.setWordWrap(True)
        self._pending_restore_details.setStyleSheet(f"color: {Colors.MUTED};")
        layout.addWidget(self._pending_restore_details)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._cancel_restore_button = SecondaryButton("Cancel pending restore", card)
        self._cancel_restore_button.setObjectName("cancelPendingRestoreButton")
        _style_secondary(self._cancel_restore_button)
        self._cancel_restore_button.clicked.connect(self._cancel_pending_restore)
        actions.addWidget(self._cancel_restore_button)
        layout.addLayout(actions)
        return card

    def _build_support_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(Spacing.MD, Spacing.MD, Spacing.MD, Spacing.MD)
        layout.setSpacing(Spacing.MD)

        card = CardWidget(tab)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        card_layout.setSpacing(Spacing.MD)
        card_layout.addWidget(SectionTitle("Support bundle", card))

        explanation = QLabel(
            "A support bundle contains sanitized diagnostics and bounded application logs. "
            "It excludes portfolio and transaction data and is never uploaded automatically.",
            card,
        )
        explanation.setObjectName("supportBundleExplanation")
        explanation.setWordWrap(True)
        explanation.setStyleSheet(f"color: {Colors.MUTED};")
        card_layout.addWidget(explanation)

        self._support_summary = QLabel("No support bundle has been created in this dialog.", card)
        self._support_summary.setObjectName("supportBundleSummary")
        self._support_summary.setWordWrap(True)
        card_layout.addWidget(self._support_summary)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self._create_support_button = PrimaryButton("Create support bundle", card)
        self._create_support_button.setObjectName("createSupportBundleButton")
        _style_primary(self._create_support_button)
        self._create_support_button.clicked.connect(self._create_support_bundle)
        actions.addWidget(self._create_support_button)
        card_layout.addLayout(actions)

        layout.addWidget(card)
        layout.addStretch(1)
        return tab

    def _load_preferences(self) -> None:
        try:
            snapshot = self._preferences_service.get_current()
        except PreferencesError:
            self._preference_status.setText(
                "Preferences could not be loaded. No preference change was made."
            )
            QMessageBox.warning(
                self,
                "Preferences unavailable",
                "Preferences could not be loaded safely. No preference change was made.",
            )
            self._refresh_control_states()
            return
        self._display_preference_snapshot(snapshot)

    def _display_preference_snapshot(self, snapshot: PreferenceSnapshot) -> None:
        self._preference_snapshot = snapshot
        self._preference_proposal = snapshot.preferences.log_level
        with QSignalBlocker(self._log_level_combo):
            index = self._log_level_combo.findData(snapshot.preferences.log_level.value)
            if index < 0:
                raise RuntimeError("The preference service returned an unsupported log level.")
            self._log_level_combo.setCurrentIndex(index)

        self._preference_load_status.setText(snapshot.status.value.replace("_", " ").capitalize())
        self._preference_format_version.setText(
            str(snapshot.format_version) if snapshot.format_version is not None else "Not present"
        )
        self._preference_file_exists.setText("Yes" if snapshot.preference_file_exists else "No")
        environment_owned = PreferenceField.LOG_LEVEL in snapshot.overridden_by_environment
        self._preference_environment.setText(
            "Log level is managed by an environment setting."
            if environment_owned
            else "Log level is managed by saved preferences or application defaults."
        )
        self._preference_restart.setText(
            "Restart required for the saved log-level change."
            if PreferenceField.LOG_LEVEL in snapshot.restart_required_fields
            else "No pending preference change requires restart."
        )
        if snapshot.warning_category is not None:
            self._preference_status.setText(
                "Stored preferences could not be applied safely; the effective value is shown."
            )
        elif environment_owned:
            self._preference_status.setText(
                "The environment-managed value is effective. Saved changes are disabled."
            )
        else:
            self._preference_status.setText(
                "Select a different log level to enable Save. Changes apply after restart."
            )
        self._refresh_control_states()

    @Slot()
    def _on_log_level_changed(self) -> None:
        self._refresh_control_states()

    def _selected_log_level(self) -> LogLevelPreference | None:
        value = self._log_level_combo.currentData()
        if not isinstance(value, str):
            return None
        try:
            return LogLevelPreference(value)
        except ValueError:
            return None

    def _preference_is_dirty(self) -> bool:
        selected = self._selected_log_level()
        return (
            selected is not None
            and self._preference_proposal is not None
            and selected is not self._preference_proposal
        )

    def _environment_owns_log_level(self) -> bool:
        snapshot = self._preference_snapshot
        return (
            snapshot is not None and PreferenceField.LOG_LEVEL in snapshot.overridden_by_environment
        )

    @Slot()
    def _save_preferences(self) -> None:
        if self.operation_in_progress or self._environment_owns_log_level():
            return
        level = self._selected_log_level()
        if level is None or not self._preference_is_dirty():
            return
        try:
            result = self._preferences_service.save(UserPreferences(log_level=level))
            snapshot = self._preferences_service.reload()
        except PreferencesError:
            self._preference_status.setText("The log-level preference could not be saved safely.")
            QMessageBox.warning(
                self,
                "Preference not saved",
                "The log-level preference could not be saved safely. No live setting changed.",
            )
            return

        self._display_preference_snapshot(snapshot)
        restart_required = PreferenceField.LOG_LEVEL in result.restart_required_fields
        message = (
            "The log-level preference was saved. Restart the application to apply the change."
            if restart_required
            else "The log-level preference was saved. The effective startup value is unchanged."
        )
        self._preference_status.setText(message)
        QMessageBox.information(self, "Preference saved", message)

    @Slot()
    def _reset_preferences(self) -> None:
        snapshot = self._preference_snapshot
        if self.operation_in_progress or snapshot is None or not snapshot.preference_file_exists:
            return
        answer = QMessageBox.question(
            self,
            "Reset saved preference",
            "Remove the saved log-level preference? The running application will not change.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            refreshed = self._preferences_service.reset()
        except PreferencesError:
            self._preference_status.setText("The saved preference could not be reset safely.")
            QMessageBox.warning(
                self,
                "Preference not reset",
                "The saved preference could not be reset safely. No live setting changed.",
            )
            return

        self._display_preference_snapshot(refreshed)
        restart_required = PreferenceField.LOG_LEVEL in refreshed.restart_required_fields
        message = (
            "The saved preference was reset. Restart the application to apply the effective value."
            if restart_required
            else "The saved preference was reset. The current effective value is unchanged."
        )
        self._preference_status.setText(message)
        QMessageBox.information(self, "Preference reset", message)

    @Slot()
    def _refresh_backups(self) -> None:
        self._start_operation(
            _REFRESH_BACKUPS,
            self._backup_service.list_backups,
            (BackupError,),
            "Refreshing verified backups…",
        )

    @Slot()
    def _create_backup(self) -> None:
        self._start_operation(
            _CREATE_BACKUP,
            partial(_create_backup_and_refresh, self._backup_service),
            (BackupError,),
            "Creating and verifying a manual backup…",
        )

    @Slot()
    def _verify_selected_backup(self) -> None:
        record = self._selected_backup()
        if record is None:
            return
        self._start_operation(
            _VERIFY_SELECTED,
            partial(self._backup_service.verify_backup, record.path),
            (BackupError,),
            "Verifying the selected backup…",
        )

    @Slot()
    def _verify_external_backup(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Verify Mira backup",
            "",
            _BACKUP_FILTER,
        )
        if not filename:
            return
        self._start_operation(
            _VERIFY_EXTERNAL,
            partial(self._backup_service.verify_backup, Path(filename)),
            (BackupError,),
            "Verifying the selected backup file…",
        )

    @Slot()
    def _stage_selected_restore(self) -> None:
        record = self._selected_backup()
        if record is None or self._pending_restore is not None:
            return
        self._confirm_and_stage(record.path, record.filename)

    @Slot()
    def _stage_external_restore(self) -> None:
        if self._pending_restore is not None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Stage Mira backup",
            "",
            _BACKUP_FILTER,
        )
        if not filename:
            return
        self._start_operation(
            _VERIFY_EXTERNAL_FOR_RESTORE,
            partial(self._backup_service.verify_backup, Path(filename)),
            (BackupError,),
            "Verifying the backup before restore staging…",
        )

    def _confirm_and_stage(self, path: Path, filename: str) -> None:
        if self._pending_restore is not None or self.operation_in_progress:
            return
        answer = QMessageBox.question(
            self,
            "Stage restore for next startup",
            _RESTORE_CONFIRMATION,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        self._start_operation(
            _STAGE_RESTORE,
            partial(_stage_restore_and_refresh, self._restore_service, path),
            (RestoreError, BackupError),
            f"Staging {_safe_filename(filename, 'the verified backup')} for next startup…",
        )

    @Slot()
    def _cancel_pending_restore(self) -> None:
        if self._pending_restore is None or self.operation_in_progress:
            return
        answer = QMessageBox.question(
            self,
            "Cancel pending restore",
            "Cancel the pending restore request? The backup archive and current database "
            "will not be changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        self._start_operation(
            _CANCEL_RESTORE,
            partial(_cancel_restore_and_refresh, self._restore_service),
            (RestoreError,),
            "Cancelling the pending restore…",
        )

    @Slot()
    def _create_support_bundle(self) -> None:
        self._start_operation(
            _CREATE_SUPPORT,
            self._diagnostics_service.create_support_bundle,
            (DiagnosticsError,),
            "Creating and verifying a privacy-safe support bundle…",
        )

    def _start_operation(
        self,
        name: str,
        operation: Callable[[], object],
        expected_errors: tuple[type[Exception], ...],
        status: str,
    ) -> None:
        if self.operation_in_progress:
            return

        thread = QThread(self)
        worker = _OperationWorker(name, operation, expected_errors)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._operation_succeeded)
        worker.expected_failure.connect(self._operation_expected_failure)
        worker.finished.connect(self._operation_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(partial(self._release_worker, thread, worker))
        thread.finished.connect(thread.deleteLater)

        self._live_threads.append(thread)
        self._live_workers.append(worker)
        self._active_operation = name
        self._after_operation = None
        self._operation_status.setText(status)
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        self._refresh_control_states()
        thread.start()

    @Slot(str, object)
    def _operation_succeeded(self, name: str, result: object) -> None:
        if name == _LOAD_RECOVERY:
            if not isinstance(result, _RecoveryState):
                raise TypeError("Recovery loading returned an invalid result.")
            self._apply_recovery_state(result)
            return
        if name == _REFRESH_BACKUPS:
            if not isinstance(result, BackupListing):
                raise TypeError("Backup refresh returned an invalid result.")
            self._display_backup_listing(result)
            self._operation_status.setText("Verified backup list refreshed.")
            return
        if name == _CREATE_BACKUP:
            if not isinstance(result, _CreatedBackup):
                raise TypeError("Backup creation returned an invalid result.")
            self._apply_created_backup(result)
            return
        if name in {_VERIFY_SELECTED, _VERIFY_EXTERNAL}:
            if not isinstance(result, BackupRecord):
                raise TypeError("Backup verification returned an invalid result.")
            self._show_verified_backup(result)
            return
        if name == _VERIFY_EXTERNAL_FOR_RESTORE:
            if not isinstance(result, BackupRecord):
                raise TypeError("Backup verification returned an invalid result.")
            self._operation_status.setText("Backup verified for restore staging.")
            self._after_operation = partial(
                self._confirm_and_stage,
                result.path,
                result.filename,
            )
            return
        if name == _STAGE_RESTORE:
            if not isinstance(result, _StagedRestore):
                raise TypeError("Restore staging returned an invalid result.")
            self._apply_staged_restore(result)
            return
        if name == _CANCEL_RESTORE:
            if not isinstance(result, _CancelledRestore):
                raise TypeError("Restore cancellation returned an invalid result.")
            self._apply_cancelled_restore(result)
            return
        if name == _CREATE_SUPPORT:
            if not isinstance(result, SupportBundleRecord):
                raise TypeError("Support creation returned an invalid result.")
            self._display_support_bundle(result)
            return
        raise RuntimeError("An unknown settings and recovery operation completed.")

    @Slot(str)
    def _operation_expected_failure(self, name: str) -> None:
        self._after_operation = None
        title, message = _expected_failure_message(name)
        self._operation_status.setText(message)
        QMessageBox.warning(self, title, message)

    @Slot(str)
    def _operation_finished(self, name: str) -> None:
        if name != self._active_operation:
            return
        self._active_operation = None
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        self._refresh_control_states()
        continuation = self._after_operation
        self._after_operation = None
        if continuation is not None:
            continuation()

    def _release_worker(self, thread: QThread, worker: _OperationWorker) -> None:
        if thread in self._live_threads:
            self._live_threads.remove(thread)
        if worker in self._live_workers:
            self._live_workers.remove(worker)

    def _apply_recovery_state(self, state: _RecoveryState) -> None:
        if state.listing is not None:
            self._display_backup_listing(state.listing)
        else:
            self._backups = ()
            self._backup_table.setRowCount(0)
            self._backup_empty_state.setText("Verified backups could not be loaded safely.")
            self._backup_empty_state.show()
            self._invalid_backup_count.setText("Invalid backup entries: unavailable")
        if state.pending is not None or not state.restore_load_failed:
            self._display_pending_restore(state.pending)
        else:
            self._pending_restore = None
            self._restore_status_available = False
            self._pending_restore_status.setText(
                "Pending restore status could not be loaded safely."
            )
            self._pending_restore_details.setText(
                "Restore staging remains unavailable until the status can be refreshed."
            )
        if state.backup_load_failed or state.restore_load_failed:
            self._operation_status.setText(
                "Some recovery status could not be loaded safely. No recovery action was run."
            )
            QMessageBox.warning(
                self,
                "Recovery status unavailable",
                "Some recovery status could not be loaded safely. No recovery action was run.",
            )
        else:
            self._operation_status.setText("Backup and restore status loaded.")
        self._refresh_control_states()

    def _display_backup_listing(
        self,
        listing: BackupListing,
        *,
        selected_filename: str | None = None,
    ) -> None:
        self._backups = listing.backups
        self._backup_table.setRowCount(0)
        selected_row = -1
        for record in listing.backups:
            row = self._backup_table.add_row(
                (
                    _format_utc(record.created_at),
                    _safe_filename(record.filename, "Verified backup"),
                    record.backup_kind.value.replace("_", " ").capitalize(),
                    _format_size(record.backup_size),
                    _format_size(record.database_size),
                    _safe_code(record.alembic_revision, "Unavailable"),
                )
            )
            if selected_filename is not None and record.filename == selected_filename:
                selected_row = row
        self._backup_empty_state.setText("No verified backups are available.")
        self._backup_empty_state.setVisible(not listing.backups)
        self._invalid_backup_count.setText(
            f"Invalid backup entries: {len(listing.invalid_backups)}"
        )
        if selected_row >= 0:
            self._backup_table.selectRow(selected_row)
            selected_item = self._backup_table.item(selected_row, 0)
            if selected_item is None:
                raise RuntimeError("A populated backup row is missing its first item.")
            self._backup_table.scrollToItem(selected_item)
        self._refresh_control_states()

    def _apply_created_backup(self, created: _CreatedBackup) -> None:
        if created.listing is not None:
            self._display_backup_listing(
                created.listing,
                selected_filename=created.record.filename,
            )
        else:
            merged = (
                created.record,
                *(record for record in self._backups if record.path != created.record.path),
            )
            self._display_backup_listing(
                BackupListing(backups=merged, invalid_backups=()),
                selected_filename=created.record.filename,
            )
        filename = _safe_filename(created.record.filename, "New backup")
        message = (
            f"Backup created: {filename}\n"
            f"Created: {_format_utc(created.record.created_at)}\n"
            f"Archive size: {_format_size(created.record.backup_size)}"
        )
        if created.refresh_failed:
            message += "\nThe full backup list could not be refreshed."
        self._operation_status.setText(
            "Backup created and selected."
            if not created.refresh_failed
            else "Backup created; the full list could not be refreshed."
        )
        QMessageBox.information(self, "Backup created", message)

    def _show_verified_backup(self, record: BackupRecord) -> None:
        message = (
            f"Backup verified: {_safe_filename(record.filename, 'Verified backup')}\n"
            f"Created: {_format_utc(record.created_at)}\n"
            f"Kind: {record.backup_kind.value.replace('_', ' ').capitalize()}\n"
            f"Archive size: {_format_size(record.backup_size)}\n"
            f"Database size: {_format_size(record.database_size)}\n"
            f"Revision: {_safe_code(record.alembic_revision, 'Unavailable')}"
        )
        self._operation_status.setText("Backup verification completed successfully.")
        QMessageBox.information(self, "Backup verified", message)

    def _apply_staged_restore(self, staged: _StagedRestore) -> None:
        self._display_pending_restore(staged.pending)
        filename = _safe_filename(staged.result.backup.filename, "Verified backup")
        message = (
            f"Backup staged: {filename}\n"
            f"Reference: {staged.result.request_id}\n"
            "Restart required. Close the application and start it again yourself."
        )
        if staged.refresh_failed:
            message += "\nThe pending status could not be independently refreshed."
        self._operation_status.setText("Restore staged. Restart required.")
        QMessageBox.information(self, "Restore staged", message)

    def _apply_cancelled_restore(self, cancelled: _CancelledRestore) -> None:
        if cancelled.refresh_failed:
            self._pending_restore = None
            self._restore_status_available = False
            self._pending_restore_status.setText(
                "Pending restore status could not be refreshed safely."
            )
            self._pending_restore_details.setText(
                "Restore staging remains unavailable until status is loaded again."
            )
        else:
            self._display_pending_restore(cancelled.pending)
        if cancelled.cancelled:
            message = "The pending restore was cancelled. The current database was not changed."
        else:
            message = "No pending restore was available to cancel."
        if cancelled.refresh_failed:
            message += " Pending status could not be refreshed safely."
        self._operation_status.setText(message)
        QMessageBox.information(self, "Pending restore", message)

    def _display_pending_restore(self, pending: RestoreStageResult | None) -> None:
        self._pending_restore = pending
        self._restore_status_available = True
        if pending is None:
            self._pending_restore_status.setText("No restore is pending")
            self._pending_restore_details.setText(
                "A verified backup can be staged for the next application start."
            )
        else:
            self._pending_restore_status.setText("Restart required")
            self._pending_restore_details.setText(
                f"Backup: {_safe_filename(pending.backup.filename, 'Verified backup')}\n"
                f"Reference: {pending.request_id}\n"
                f"Staged: {_format_utc(pending.staged_at)}\n"
                f"Revision: {_safe_code(pending.backup.alembic_revision, 'Unavailable')}"
            )
        self._refresh_control_states()

    def _display_support_bundle(self, record: SupportBundleRecord) -> None:
        summary = (
            f"Filename: {_safe_filename(record.filename, 'Support bundle')}\n"
            f"Created: {_format_utc(record.created_at)}\n"
            f"Members: {record.member_count}\n"
            f"Archive size: {_format_size(record.archive_size_bytes)}\n"
            f"Reference: {record.bundle_id}"
        )
        self._support_summary.setText(summary)
        self._operation_status.setText("Support bundle created and verified.")
        QMessageBox.information(self, "Support bundle created", summary)

    def _selected_backup(self) -> BackupRecord | None:
        row = self._backup_table.currentRow()
        if row < 0 or row >= len(self._backups):
            return None
        return self._backups[row]

    def _refresh_control_states(self) -> None:
        busy = self.operation_in_progress
        selected_backup = self._selected_backup() is not None
        pending = self._pending_restore is not None
        preference_ready = self._preference_snapshot is not None
        environment_owned = self._environment_owns_log_level()

        self._log_level_combo.setEnabled(preference_ready and not environment_owned and not busy)
        self._save_preferences_button.setEnabled(
            preference_ready and not environment_owned and not busy and self._preference_is_dirty()
        )
        self._reset_preferences_button.setEnabled(
            preference_ready
            and self._preference_snapshot is not None
            and self._preference_snapshot.preference_file_exists
            and not busy
        )
        self._refresh_backups_button.setEnabled(not busy)
        self._create_backup_button.setEnabled(not busy)
        self._verify_selected_button.setEnabled(selected_backup and not busy)
        self._verify_file_button.setEnabled(not busy)
        self._stage_selected_button.setEnabled(
            selected_backup and self._restore_status_available and not pending and not busy
        )
        self._stage_file_button.setEnabled(
            self._restore_status_available and not pending and not busy
        )
        self._cancel_restore_button.setEnabled(
            self._restore_status_available and pending and not busy
        )
        self._create_support_button.setEnabled(not busy)
        self._close_button.setEnabled(not busy)

    def _connect_shutdown_wait(self) -> None:
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.aboutToQuit.connect(self._wait_for_workers)

    @Slot()
    def _wait_for_workers(self) -> None:
        for thread in tuple(self._live_threads):
            if thread.isRunning():
                thread.quit()
                thread.wait()

    def _workers_are_running(self) -> bool:
        return any(thread.isRunning() for thread in self._live_threads)

    def _confirm_discard(self) -> bool:
        if not self._preference_is_dirty():
            return True
        answer = QMessageBox.question(
            self,
            "Discard unsaved preference",
            "Discard the unsaved log-level selection? Nothing will be saved.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer is QMessageBox.StandardButton.Yes

    def reject(self) -> None:
        """Keep owned workers alive and make dirty-selection discard explicit."""
        if self._workers_are_running():
            self._operation_status.setText(
                "Wait for the current recovery operation to finish before closing."
            )
            return
        if self._confirm_discard():
            super().reject()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Prevent destruction while a service call is owned by the dialog."""
        if self._workers_are_running():
            self._operation_status.setText(
                "Wait for the current recovery operation to finish before closing."
            )
            event.ignore()
            return
        if not self._confirm_discard():
            event.ignore()
            return
        super().closeEvent(event)


def _expected_failure_message(name: str) -> tuple[str, str]:
    if name in {_REFRESH_BACKUPS, _LOAD_RECOVERY}:
        return (
            "Backups unavailable",
            "Verified backups could not be loaded safely. No recovery action was run.",
        )
    if name == _CREATE_BACKUP:
        return (
            "Backup not created",
            "The manual backup could not be created and verified safely.",
        )
    if name in {_VERIFY_SELECTED, _VERIFY_EXTERNAL, _VERIFY_EXTERNAL_FOR_RESTORE}:
        return (
            "Backup not verified",
            "The selected backup could not be verified. Choose a valid Mira backup.",
        )
    if name == _STAGE_RESTORE:
        return (
            "Restore not staged",
            "The verified backup could not be staged for restart. Check pending restore status.",
        )
    if name == _CANCEL_RESTORE:
        return (
            "Restore not cancelled",
            "The pending restore could not be cancelled safely. Contact support if it persists.",
        )
    if name == _CREATE_SUPPORT:
        return (
            "Support bundle not created",
            "The privacy-safe support bundle could not be created and verified.",
        )
    raise RuntimeError("An unknown settings and recovery operation failed.")


def _value_label(object_name: str, parent: QWidget) -> QLabel:
    label = QLabel("Loading…", parent)
    label.setObjectName(object_name)
    return label


def _style_primary(button: PrimaryButton) -> None:
    button.setStyleSheet(f"background: {Colors.ACCENT};")


def _style_secondary(button: SecondaryButton) -> None:
    button.setStyleSheet(f"background: {Colors.SURFACE_RAISED}; border: 1px solid {Colors.BORDER};")


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("A service timestamp must be timezone-aware.")
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_size(size: int) -> str:
    if isinstance(size, bool) or size < 0:
        raise ValueError("A service file size must be a non-negative integer.")
    units = ((1024 * 1024 * 1024, "GiB"), (1024 * 1024, "MiB"), (1024, "KiB"))
    for unit_size, unit_name in units:
        if size >= unit_size:
            whole = size // unit_size
            tenth = ((size % unit_size) * 10) // unit_size
            return f"{whole}.{tenth} {unit_name}"
    return f"{size} B"


def _safe_filename(value: str, fallback: str) -> str:
    if (
        not value
        or len(value) > 180
        or "/" in value
        or "\\" in value
        or any(not character.isprintable() for character in value)
    ):
        return fallback
    return value


def _safe_code(value: str, fallback: str) -> str:
    if not value or len(value) > 80:
        return fallback
    if not all(character.isalnum() or character in "._+-" for character in value):
        return fallback
    return value


__all__ = ["SettingsRecoveryDialog"]
