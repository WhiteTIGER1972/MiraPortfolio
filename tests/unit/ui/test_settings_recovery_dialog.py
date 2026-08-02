"""Focused behavior tests for the Settings and Recovery Tools dialog."""

from __future__ import annotations

import inspect
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import TracebackType
from typing import cast
from uuid import UUID

import pytest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTabWidget,
)

from app.application.backup import (
    BackupKind,
    BackupListing,
    BackupRecord,
    InvalidBackup,
)
from app.application.diagnostics import SupportBundleRecord
from app.application.preferences import (
    LogLevelPreference,
    PreferenceField,
    PreferenceLoadStatus,
    PreferenceSaveResult,
    PreferenceSnapshot,
    UserPreferences,
)
from app.application.restore import (
    RestoreBackupIdentity,
    RestoreOutcome,
    RestoreStageResult,
)
from app.core.exceptions import (
    BackupCreationError,
    BackupVerificationError,
    PreferencePersistenceError,
    RestoreStagingError,
    SupportBundleCreationError,
)
from app.ui.dialogs import settings_recovery_dialog as dialog_module
from app.ui.dialogs.settings_recovery_dialog import SettingsRecoveryDialog

NOW = datetime(2026, 8, 2, 10, 15, 30, tzinfo=UTC)
REQUEST_ID = UUID("11111111-2222-3333-4444-555555555555")
BUNDLE_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def preference_snapshot(
    *,
    level: LogLevelPreference = LogLevelPreference.INFO,
    exists: bool = False,
    overridden: bool = False,
    restart: bool = False,
    status: PreferenceLoadStatus = PreferenceLoadStatus.MISSING,
    format_version: int | None = None,
) -> PreferenceSnapshot:
    fields = frozenset({PreferenceField.LOG_LEVEL})
    return PreferenceSnapshot(
        preferences=UserPreferences(log_level=level),
        status=status,
        format_version=format_version,
        preference_file_exists=exists,
        restart_required_fields=fields if restart else frozenset(),
        overridden_by_environment=fields if overridden else frozenset(),
        warning_category=None,
    )


def backup_record(
    root: Path,
    filename: str = "mira-portfolio-manual-20260802T101530Z-a1b2c3d4e5f6.mirabackup",
    *,
    created_at: datetime = NOW,
) -> BackupRecord:
    return BackupRecord(
        path=root / filename,
        filename=filename,
        created_at=created_at,
        app_version="0.1.0",
        alembic_revision="1234abcd",
        database_size=2 * 1024 * 1024,
        database_sha256="a" * 64,
        backup_size=1024 * 1024,
        backup_kind=BackupKind.MANUAL,
    )


def pending_restore(record: BackupRecord) -> RestoreStageResult:
    return RestoreStageResult(
        request_id=REQUEST_ID,
        backup=RestoreBackupIdentity(
            filename=record.filename,
            app_version=record.app_version,
            alembic_revision=record.alembic_revision,
            database_size=record.database_size,
            database_sha256=record.database_sha256,
            backup_kind=record.backup_kind,
        ),
        staged_at=NOW,
        restart_required=True,
        outcome=RestoreOutcome.STAGED,
    )


def support_record(root: Path) -> SupportBundleRecord:
    filename = "mira-portfolio-support-20260802T101530Z-a1b2c3d4e5f6.mirasupport"
    return SupportBundleRecord(
        path=root / filename,
        filename=filename,
        created_at=NOW,
        bundle_id=BUNDLE_ID,
        application_version="0.1.0",
        member_count=6,
        archive_size_bytes=3 * 1024,
        archive_sha256="b" * 64,
    )


class FakePreferencesService:
    def __init__(self, snapshot: PreferenceSnapshot) -> None:
        self.snapshot = snapshot
        self.running_level = snapshot.preferences.log_level
        self.get_calls = 0
        self.save_calls: list[UserPreferences] = []
        self.reset_calls = 0
        self.reload_calls = 0
        self.get_error: Exception | None = None
        self.save_error: Exception | None = None
        self.reset_error: Exception | None = None
        self.reload_error: Exception | None = None

    def get_current(self) -> PreferenceSnapshot:
        self.get_calls += 1
        if self.get_error is not None:
            raise self.get_error
        return self.snapshot

    def save(self, preferences: UserPreferences) -> PreferenceSaveResult:
        self.save_calls.append(preferences)
        if self.save_error is not None:
            raise self.save_error
        restart = (
            frozenset({PreferenceField.LOG_LEVEL})
            if preferences.log_level is not self.running_level
            else frozenset()
        )
        self.snapshot = PreferenceSnapshot(
            preferences=preferences,
            status=PreferenceLoadStatus.LOADED,
            format_version=2,
            preference_file_exists=True,
            restart_required_fields=restart,
            overridden_by_environment=self.snapshot.overridden_by_environment,
            warning_category=None,
        )
        return PreferenceSaveResult(
            preferences=preferences,
            saved_at_utc=NOW,
            restart_required_fields=restart,
            overridden_by_environment=self.snapshot.overridden_by_environment,
        )

    def reset(self) -> PreferenceSnapshot:
        self.reset_calls += 1
        if self.reset_error is not None:
            raise self.reset_error
        self.snapshot = preference_snapshot(level=self.running_level, restart=False)
        return self.snapshot

    def reload(self) -> PreferenceSnapshot:
        self.reload_calls += 1
        if self.reload_error is not None:
            raise self.reload_error
        return self.snapshot


class FakeBackupService:
    def __init__(self, listing: BackupListing, created: BackupRecord) -> None:
        self.listing = listing
        self.created = created
        self.list_calls = 0
        self.create_calls: list[BackupKind] = []
        self.verify_calls: list[Path] = []
        self.list_error: Exception | None = None
        self.create_error: Exception | None = None
        self.verify_error: Exception | None = None
        self.create_started: Event | None = None
        self.create_release: Event | None = None
        self.events: list[str] = []

    def list_backups(self) -> BackupListing:
        self.list_calls += 1
        self.events.append("list")
        if self.list_error is not None:
            raise self.list_error
        return self.listing

    def create_backup(self, kind: BackupKind = BackupKind.MANUAL) -> BackupRecord:
        self.create_calls.append(kind)
        self.events.append("create")
        if self.create_started is not None:
            self.create_started.set()
        if self.create_release is not None:
            self.create_release.wait(timeout=5)
        if self.create_error is not None:
            raise self.create_error
        self.listing = BackupListing((self.created, *self.listing.backups), ())
        return self.created

    def verify_backup(self, path: Path) -> BackupRecord:
        self.verify_calls.append(path)
        self.events.append("verify")
        if self.verify_error is not None:
            raise self.verify_error
        for record in (*self.listing.backups, self.created):
            if record.path == path:
                return record
        return replace(self.created, path=path, filename=path.name)


class FakeRestoreService:
    def __init__(self, pending: RestoreStageResult | None = None) -> None:
        self.pending = pending
        self.get_calls = 0
        self.stage_calls: list[Path] = []
        self.cancel_calls = 0
        self.get_error: Exception | None = None
        self.stage_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.events: list[str] = []

    def stage_restore(self, backup_path: Path) -> RestoreStageResult:
        self.stage_calls.append(backup_path)
        self.events.append("stage")
        if self.stage_error is not None:
            raise self.stage_error
        record = BackupRecord(
            path=backup_path,
            filename=backup_path.name,
            created_at=NOW,
            app_version="0.1.0",
            alembic_revision="1234abcd",
            database_size=2048,
            database_sha256="a" * 64,
            backup_size=1024,
            backup_kind=BackupKind.MANUAL,
        )
        self.pending = pending_restore(record)
        return self.pending

    def get_pending_restore(self) -> RestoreStageResult | None:
        self.get_calls += 1
        self.events.append("pending")
        if self.get_error is not None:
            raise self.get_error
        return self.pending

    def cancel_pending_restore(self) -> bool:
        self.cancel_calls += 1
        self.events.append("cancel")
        if self.cancel_error is not None:
            raise self.cancel_error
        existed = self.pending is not None
        self.pending = None
        return existed


class FakeDiagnosticsService:
    def __init__(self, record: SupportBundleRecord) -> None:
        self.record = record
        self.create_calls = 0
        self.error: Exception | None = None
        self.started: Event | None = None
        self.release: Event | None = None

    def create_support_bundle(self) -> SupportBundleRecord:
        self.create_calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            self.release.wait(timeout=5)
        if self.error is not None:
            raise self.error
        return self.record


@dataclass(slots=True)
class MessageRecorder:
    information: list[tuple[str, str]]
    warnings: list[tuple[str, str]]
    questions: list[tuple[str, str]]
    answers: list[QMessageBox.StandardButton]


@pytest.fixture
def messages(monkeypatch: pytest.MonkeyPatch) -> MessageRecorder:
    recorder = MessageRecorder([], [], [], [])

    def information(
        _parent: object,
        title: str,
        text: str,
        *_args: object,
    ) -> QMessageBox.StandardButton:
        recorder.information.append((title, text))
        return QMessageBox.StandardButton.Ok

    def warning(
        _parent: object,
        title: str,
        text: str,
        *_args: object,
    ) -> QMessageBox.StandardButton:
        recorder.warnings.append((title, text))
        return QMessageBox.StandardButton.Ok

    def question(
        _parent: object,
        title: str,
        text: str,
        *_args: object,
    ) -> QMessageBox.StandardButton:
        recorder.questions.append((title, text))
        if recorder.answers:
            return recorder.answers.pop(0)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "information", information)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    monkeypatch.setattr(QMessageBox, "question", question)
    return recorder


@dataclass(slots=True)
class DialogHarness:
    dialog: SettingsRecoveryDialog
    preferences: FakePreferencesService
    backups: FakeBackupService
    restores: FakeRestoreService
    diagnostics: FakeDiagnosticsService


class DialogFactory:
    def __init__(self, application: QApplication, root: Path) -> None:
        self.application = application
        self.root = root
        self.dialogs: list[SettingsRecoveryDialog] = []

    def __call__(
        self,
        *,
        snapshot: PreferenceSnapshot | None = None,
        listing: BackupListing | None = None,
        pending: RestoreStageResult | None = None,
    ) -> DialogHarness:
        created = backup_record(self.root)
        preferences = FakePreferencesService(snapshot or preference_snapshot())
        backups = FakeBackupService(listing or BackupListing((), ()), created)
        restores = FakeRestoreService(pending)
        diagnostics = FakeDiagnosticsService(support_record(self.root))
        dialog = SettingsRecoveryDialog(
            cast(object, preferences),
            cast(object, backups),
            cast(object, restores),
            cast(object, diagnostics),
        )
        self.dialogs.append(dialog)
        wait_until(self.application, lambda: not dialog.operation_in_progress)
        wait_until(self.application, lambda: dialog.running_worker_count == 0)
        return DialogHarness(dialog, preferences, backups, restores, diagnostics)

    def close_all(self) -> None:
        for dialog in self.dialogs:
            if dialog.running_worker_count:
                dialog._wait_for_workers()
            if dialog._preference_is_dirty():
                combo = child(dialog, QComboBox, "logLevelCombo")
                combo.setCurrentIndex(combo.findData(dialog._preference_proposal.value))
            dialog.close()
            dialog.deleteLater()
        self.application.processEvents()


@pytest.fixture
def make_dialog(qapplication: QApplication, tmp_path: Path) -> DialogFactory:
    factory = DialogFactory(qapplication, tmp_path)
    yield factory
    factory.close_all()


def wait_until(
    application: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout: float = 5,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            application.processEvents()
            if predicate():
                return
        time.sleep(0.001)
    raise AssertionError("Timed out waiting for the Qt worker state.")


def child[WidgetT](
    parent: SettingsRecoveryDialog,
    widget_type: type[WidgetT],
    name: str,
) -> WidgetT:
    widget = parent.findChild(widget_type, name)
    assert widget is not None
    return widget


def visible_text(dialog: SettingsRecoveryDialog) -> str:
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    buttons = [button.text() for button in dialog.findChildren(QPushButton)]
    tabs = child(dialog, QTabWidget, "settingsRecoveryTabs")
    tab_text = [tabs.tabText(index) for index in range(tabs.count())]
    table_text: list[str] = []
    for table in dialog.findChildren(QTableWidget):
        for row in range(table.rowCount()):
            for column in range(table.columnCount()):
                item = table.item(row, column)
                if item is not None:
                    table_text.append(item.text())
    return "\n".join((*labels, *buttons, *tab_text, *table_text))


def test_dialog_structure_object_names_and_narrow_constructor(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
) -> None:
    harness = make_dialog()
    dialog = harness.dialog

    assert dialog.objectName() == "settingsRecoveryDialog"
    tabs = child(dialog, QTabWidget, "settingsRecoveryTabs")
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "Preferences",
        "Backup & Restore",
        "Support",
    ]
    for name in (
        "logLevelCombo",
        "savePreferencesButton",
        "resetPreferencesButton",
        "backupTable",
        "refreshBackupsButton",
        "createBackupButton",
        "verifySelectedBackupButton",
        "verifyBackupFileButton",
        "stageSelectedRestoreButton",
        "stageRestoreFileButton",
        "cancelPendingRestoreButton",
        "createSupportBundleButton",
        "closeSettingsRecoveryButton",
    ):
        assert dialog.findChild(object, name) is not None
    assert list(inspect.signature(SettingsRecoveryDialog).parameters) == [
        "preferences_service",
        "backup_service",
        "restore_service",
        "diagnostics_service",
        "parent",
    ]
    text = visible_text(dialog).lower()
    for forbidden in (
        "theme",
        "auto backup",
        "auto_backup",
        "auto snapshot",
        "auto_snapshot",
        "default currency",
        "database path",
        "language selector",
        "raw json",
    ):
        assert forbidden not in text
    assert "excludes portfolio and transaction data" in text
    assert messages.warnings == []


def test_initialization_performs_only_safe_reads(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
) -> None:
    harness = make_dialog()

    assert harness.preferences.get_calls == 1
    assert harness.backups.list_calls == 1
    assert harness.restores.get_calls == 1
    assert harness.preferences.save_calls == []
    assert harness.preferences.reset_calls == 0
    assert harness.backups.create_calls == []
    assert harness.backups.verify_calls == []
    assert harness.restores.stage_calls == []
    assert harness.restores.cancel_calls == 0
    assert harness.diagnostics.create_calls == 0
    assert messages.information == []


def test_preferences_render_every_level_and_source_metadata(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
) -> None:
    harness = make_dialog(
        snapshot=preference_snapshot(
            level=LogLevelPreference.WARNING,
            exists=True,
            status=PreferenceLoadStatus.LOADED,
            format_version=2,
        )
    )
    dialog = harness.dialog
    combo = child(dialog, QComboBox, "logLevelCombo")

    assert [combo.itemData(index) for index in range(combo.count())] == [
        level.value for level in LogLevelPreference
    ]
    assert combo.currentData() == "WARNING"
    assert child(dialog, QLabel, "preferenceLoadStatus").text() == "Loaded"
    assert child(dialog, QLabel, "preferenceFormatVersion").text() == "2"
    assert child(dialog, QLabel, "preferenceFileExists").text() == "Yes"
    assert "restart" in child(dialog, QLabel, "preferenceRestartRequired").text().lower()


def test_environment_owned_preference_is_truthful_and_not_editable(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
) -> None:
    harness = make_dialog(
        snapshot=preference_snapshot(
            level=LogLevelPreference.ERROR,
            exists=True,
            overridden=True,
            status=PreferenceLoadStatus.LOADED,
            format_version=2,
        )
    )
    dialog = harness.dialog

    assert not child(dialog, QComboBox, "logLevelCombo").isEnabled()
    assert not child(dialog, QPushButton, "savePreferencesButton").isEnabled()
    assert child(dialog, QPushButton, "resetPreferencesButton").isEnabled()
    environment = child(dialog, QLabel, "preferenceEnvironmentOverride").text()
    assert environment == "Log level is managed by an environment setting."
    assert "ERROR" not in environment
    child(dialog, QPushButton, "savePreferencesButton").click()
    assert harness.preferences.save_calls == []


def test_dirty_save_persists_one_log_level_reloads_and_requires_restart(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
) -> None:
    harness = make_dialog(snapshot=preference_snapshot(level=LogLevelPreference.INFO))
    dialog = harness.dialog
    combo = child(dialog, QComboBox, "logLevelCombo")
    save = child(dialog, QPushButton, "savePreferencesButton")

    assert not save.isEnabled()
    combo.setCurrentIndex(combo.findData(LogLevelPreference.WARNING.value))
    assert save.isEnabled()
    save.click()

    assert harness.preferences.save_calls == [UserPreferences(log_level=LogLevelPreference.WARNING)]
    assert harness.preferences.reload_calls == 1
    assert harness.preferences.running_level is LogLevelPreference.INFO
    assert combo.currentData() == LogLevelPreference.WARNING.value
    assert not save.isEnabled()
    assert messages.information[-1][0] == "Preference saved"
    assert "restart" in messages.information[-1][1].lower()
    assert "WARNING" not in messages.information[-1][1]


def test_preference_errors_are_fixed_and_unexpected_errors_escape(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
) -> None:
    harness = make_dialog(snapshot=preference_snapshot())
    combo = child(harness.dialog, QComboBox, "logLevelCombo")
    combo.setCurrentIndex(combo.findData(LogLevelPreference.DEBUG.value))
    harness.preferences.save_error = PreferencePersistenceError(
        "C:\\private\\preferences.json secret-value"
    )

    child(harness.dialog, QPushButton, "savePreferencesButton").click()
    assert messages.warnings[-1] == (
        "Preference not saved",
        "The log-level preference could not be saved safely. No live setting changed.",
    )
    assert "private" not in visible_text(harness.dialog)

    harness.preferences.save_error = RuntimeError("unexpected sentinel")
    with pytest.raises(RuntimeError, match="unexpected sentinel"):
        harness.dialog._save_preferences()


def test_reset_confirmation_cancel_and_confirm_are_exact(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
) -> None:
    harness = make_dialog(
        snapshot=preference_snapshot(
            level=LogLevelPreference.WARNING,
            exists=True,
            status=PreferenceLoadStatus.LOADED,
            format_version=2,
        )
    )
    reset = child(harness.dialog, QPushButton, "resetPreferencesButton")
    messages.answers.extend([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])

    reset.click()
    assert harness.preferences.reset_calls == 0
    reset.click()
    assert harness.preferences.reset_calls == 1
    assert child(harness.dialog, QLabel, "preferenceFileExists").text() == "No"
    assert not reset.isEnabled()
    assert messages.questions[0][0] == "Reset saved preference"
    assert messages.information[-1][0] == "Preference reset"


def test_reset_expected_error_is_sanitized(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
) -> None:
    harness = make_dialog(snapshot=preference_snapshot(exists=True))
    harness.preferences.reset_error = PreferencePersistenceError("D:\\secret\\prefs")
    child(harness.dialog, QPushButton, "resetPreferencesButton").click()

    assert harness.preferences.reset_calls == 1
    assert messages.warnings[-1][0] == "Preference not reset"
    assert "secret" not in messages.warnings[-1][1]


def test_unsaved_close_requires_discard_and_never_saves(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
) -> None:
    harness = make_dialog()
    dialog = harness.dialog
    combo = child(dialog, QComboBox, "logLevelCombo")
    combo.setCurrentIndex(combo.findData(LogLevelPreference.ERROR.value))
    dialog.setResult(QDialog.DialogCode.Accepted)
    messages.answers.extend([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])

    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert harness.preferences.save_calls == []
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert harness.preferences.save_calls == []
    assert [title for title, _ in messages.questions] == [
        "Discard unsaved preference",
        "Discard unsaved preference",
    ]


def test_backup_table_is_read_only_newest_order_and_path_free(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    tmp_path: Path,
) -> None:
    newest = backup_record(tmp_path, "newest.mirabackup", created_at=NOW)
    oldest = backup_record(
        tmp_path,
        "oldest.mirabackup",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    harness = make_dialog(
        listing=BackupListing(
            (newest, oldest),
            (InvalidBackup("do-not-render.mirabackup", "D:\\secret"),),
        )
    )
    table = child(harness.dialog, QTableWidget, "backupTable")

    assert table.rowCount() == 2
    assert table.columnCount() == 6
    assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert table.item(0, 1).text() == "newest.mirabackup"
    assert table.item(1, 1).text() == "oldest.mirabackup"
    assert table.item(0, 0).text().endswith(" UTC")
    assert table.item(0, 3).text() == "1.0 MiB"
    assert table.item(0, 4).text() == "2.0 MiB"
    text = visible_text(harness.dialog)
    assert str(tmp_path) not in text
    assert "a" * 64 not in text
    assert "do-not-render" not in text
    assert "Invalid backup entries: 1" in text


def test_empty_backup_state_and_selection_control_availability(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
) -> None:
    harness = make_dialog()
    dialog = harness.dialog

    assert not child(dialog, QLabel, "backupEmptyState").isHidden()
    assert not child(dialog, QPushButton, "verifySelectedBackupButton").isEnabled()
    assert not child(dialog, QPushButton, "stageSelectedRestoreButton").isEnabled()
    assert child(dialog, QPushButton, "verifyBackupFileButton").isEnabled()
    assert child(dialog, QPushButton, "stageRestoreFileButton").isEnabled()


def test_create_backup_has_busy_lifecycle_prevents_duplicates_and_selects_result(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
) -> None:
    harness = make_dialog()
    dialog = harness.dialog
    started = Event()
    release = Event()
    harness.backups.create_started = started
    harness.backups.create_release = release
    create = child(dialog, QPushButton, "createBackupButton")

    create.click()
    wait_until(qapplication, started.is_set)
    assert dialog.operation_in_progress
    assert not create.isEnabled()
    assert not child(dialog, QPushButton, "closeSettingsRecoveryButton").isEnabled()
    assert QApplication.overrideCursor() is not None
    create.click()
    dialog._create_backup()
    dialog.setResult(QDialog.DialogCode.Accepted)
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert harness.backups.create_calls == [BackupKind.MANUAL]

    release.set()
    wait_until(qapplication, lambda: not dialog.operation_in_progress)
    wait_until(qapplication, lambda: dialog.running_worker_count == 0)
    assert QApplication.overrideCursor() is None
    assert create.isEnabled()
    assert harness.backups.list_calls == 2
    table = child(dialog, QTableWidget, "backupTable")
    assert table.rowCount() == 1
    assert table.currentRow() == 0
    assert messages.information[-1][0] == "Backup created"
    assert str(harness.backups.created.path.parent) not in messages.information[-1][1]


def test_backup_expected_error_restores_state_without_leaking_details(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
) -> None:
    harness = make_dialog()
    harness.backups.create_error = BackupCreationError("C:\\private\\portfolio.db token")

    child(harness.dialog, QPushButton, "createBackupButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)

    assert messages.warnings[-1] == (
        "Backup not created",
        "The manual backup could not be created and verified safely.",
    )
    assert child(harness.dialog, QPushButton, "createBackupButton").isEnabled()
    assert QApplication.overrideCursor() is None
    assert "private" not in visible_text(harness.dialog)


def test_backup_refresh_is_explicit_and_expected_failure_is_safe(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
    tmp_path: Path,
) -> None:
    harness = make_dialog()
    refreshed = backup_record(tmp_path, "refreshed.mirabackup")
    harness.backups.listing = BackupListing((refreshed,), ())

    child(harness.dialog, QPushButton, "refreshBackupsButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)
    assert harness.backups.list_calls == 2
    assert child(harness.dialog, QTableWidget, "backupTable").item(0, 1).text() == (
        "refreshed.mirabackup"
    )

    harness.backups.list_error = BackupVerificationError("D:\\secret\\archive")
    child(harness.dialog, QPushButton, "refreshBackupsButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)
    assert harness.backups.list_calls == 3
    assert messages.warnings[-1][0] == "Backups unavailable"
    assert "secret" not in messages.warnings[-1][1]
    assert child(harness.dialog, QPushButton, "refreshBackupsButton").isEnabled()


def test_unexpected_worker_error_reaches_process_hook(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = make_dialog()
    harness.backups.create_error = RuntimeError("unexpected worker sentinel")
    seen: list[BaseException] = []

    def hook(
        _error_type: type[BaseException],
        error: BaseException,
        _traceback: TracebackType | None,
    ) -> None:
        seen.append(error)

    monkeypatch.setattr(sys, "excepthook", hook)
    child(harness.dialog, QPushButton, "createBackupButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)
    wait_until(qapplication, lambda: bool(seen))

    assert isinstance(seen[0], RuntimeError)
    assert str(seen[0]) == "unexpected worker sentinel"
    assert messages.warnings == []


def test_selected_and_external_backup_verification_use_service_only(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    listed = backup_record(tmp_path, "listed.mirabackup")
    external = tmp_path / "external.mirabackup"
    harness = make_dialog(listing=BackupListing((listed,), ()))
    table = child(harness.dialog, QTableWidget, "backupTable")
    table.selectRow(0)

    child(harness.dialog, QPushButton, "verifySelectedBackupButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)
    assert harness.backups.verify_calls == [listed.path]
    assert "listed.mirabackup" in messages.information[-1][1]
    assert str(tmp_path) not in messages.information[-1][1]

    captured_filters: list[str] = []

    def choose(*args: object) -> tuple[str, str]:
        captured_filters.append(cast(str, args[3]))
        return str(external), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    child(harness.dialog, QPushButton, "verifyBackupFileButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)
    assert harness.backups.verify_calls[-1] == external
    assert captured_filters == ["Mira backup (*.mirabackup);;All files (*)"]
    assert "external.mirabackup" in messages.information[-1][1]
    assert str(tmp_path) not in messages.information[-1][1]


def test_cancelled_external_file_dialog_calls_no_service(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = make_dialog()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: ("", ""))

    child(harness.dialog, QPushButton, "verifyBackupFileButton").click()
    child(harness.dialog, QPushButton, "stageRestoreFileButton").click()
    assert harness.backups.verify_calls == []
    assert harness.restores.stage_calls == []


def test_verification_failure_is_sanitized_and_does_not_stage(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    harness = make_dialog()
    harness.backups.verify_error = BackupVerificationError("D:\\secret\\bad.mirabackup hash-value")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args: (str(tmp_path / "bad.mirabackup"), ""),
    )

    child(harness.dialog, QPushButton, "stageRestoreFileButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)

    assert messages.warnings[-1][0] == "Backup not verified"
    assert "secret" not in messages.warnings[-1][1]
    assert harness.restores.stage_calls == []
    assert messages.questions == []


def test_restore_confirmation_is_complete_and_cancelled_stage_is_noop(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    tmp_path: Path,
) -> None:
    listed = backup_record(tmp_path, "restore-source.mirabackup")
    harness = make_dialog(listing=BackupListing((listed,), ()))
    child(harness.dialog, QTableWidget, "backupTable").selectRow(0)
    messages.answers.append(QMessageBox.StandardButton.No)

    child(harness.dialog, QPushButton, "stageSelectedRestoreButton").click()

    assert harness.restores.stage_calls == []
    confirmation = messages.questions[-1][1].lower()
    for required in (
        "current database will not change now",
        "next application start",
        "pre-restore safety backup",
        "close the application",
        "start it again yourself",
    ):
        assert required in confirmation
    assert "automatically restart" not in confirmation


def test_selected_restore_stages_for_restart_and_disables_further_staging(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
    tmp_path: Path,
) -> None:
    listed = backup_record(tmp_path, "restore-source.mirabackup")
    harness = make_dialog(listing=BackupListing((listed,), ()))
    child(harness.dialog, QTableWidget, "backupTable").selectRow(0)

    child(harness.dialog, QPushButton, "stageSelectedRestoreButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)

    assert harness.restores.stage_calls == [listed.path]
    assert harness.restores.get_calls == 2
    assert child(harness.dialog, QLabel, "pendingRestoreStatus").text() == "Restart required"
    details = child(harness.dialog, QLabel, "pendingRestoreDetails").text()
    assert "restore-source.mirabackup" in details
    assert str(REQUEST_ID) in details
    assert "1234abcd" in details
    assert "a" * 64 not in details
    assert str(tmp_path) not in details
    assert not child(harness.dialog, QPushButton, "stageSelectedRestoreButton").isEnabled()
    assert not child(harness.dialog, QPushButton, "stageRestoreFileButton").isEnabled()
    assert child(harness.dialog, QPushButton, "cancelPendingRestoreButton").isEnabled()
    assert messages.information[-1][0] == "Restore staged"
    assert "Restart required" in messages.information[-1][1]


def test_external_restore_verifies_then_confirms_then_stages(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    external = tmp_path / "external-restore.mirabackup"
    harness = make_dialog()
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args: (str(external), ""),
    )

    child(harness.dialog, QPushButton, "stageRestoreFileButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)

    assert harness.backups.verify_calls == [external]
    assert harness.restores.stage_calls == [external]
    assert harness.backups.events[-1] == "verify"
    assert harness.restores.events[-2:] == ["stage", "pending"]
    assert messages.questions[-1][0] == "Stage restore for next startup"
    assert child(harness.dialog, QLabel, "pendingRestoreStatus").text() == "Restart required"


def test_pending_restore_initial_state_is_safe_and_cancellation_is_confirmed(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
    tmp_path: Path,
) -> None:
    record = backup_record(tmp_path, "pending.mirabackup")
    harness = make_dialog(pending=pending_restore(record))
    dialog = harness.dialog

    assert child(dialog, QLabel, "pendingRestoreStatus").text() == "Restart required"
    assert not child(dialog, QPushButton, "stageRestoreFileButton").isEnabled()
    messages.answers.extend([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
    cancel = child(dialog, QPushButton, "cancelPendingRestoreButton")
    cancel.click()
    assert harness.restores.cancel_calls == 0
    cancel.click()
    wait_until(qapplication, lambda: not dialog.operation_in_progress)

    assert harness.restores.cancel_calls == 1
    assert child(dialog, QLabel, "pendingRestoreStatus").text() == "No restore is pending"
    assert child(dialog, QPushButton, "stageRestoreFileButton").isEnabled()
    assert not cancel.isEnabled()
    assert messages.information[-1][0] == "Pending restore"


def test_restore_expected_errors_are_sanitized(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
    tmp_path: Path,
) -> None:
    listed = backup_record(tmp_path, "restore-source.mirabackup")
    harness = make_dialog(listing=BackupListing((listed,), ()))
    harness.restores.stage_error = RestoreStagingError(
        "sqlite:///D:/secret/portfolio.db credential"
    )
    child(harness.dialog, QTableWidget, "backupTable").selectRow(0)

    child(harness.dialog, QPushButton, "stageSelectedRestoreButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)

    assert messages.warnings[-1][0] == "Restore not staged"
    assert "secret" not in messages.warnings[-1][1]
    assert child(harness.dialog, QLabel, "pendingRestoreStatus").text() == ("No restore is pending")


def test_cancel_restore_failure_is_sanitized_and_keeps_backup_record(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
    tmp_path: Path,
) -> None:
    record = backup_record(tmp_path, "retained.mirabackup")
    harness = make_dialog(
        listing=BackupListing((record,), ()),
        pending=pending_restore(record),
    )
    harness.restores.cancel_error = RestoreStagingError("D:\\secret\\restore")

    child(harness.dialog, QPushButton, "cancelPendingRestoreButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)

    assert harness.restores.cancel_calls == 1
    assert messages.warnings[-1][0] == "Restore not cancelled"
    assert "secret" not in messages.warnings[-1][1]
    assert child(harness.dialog, QTableWidget, "backupTable").rowCount() == 1
    assert harness.backups.listing.backups == (record,)


def test_support_bundle_is_explicit_sanitized_and_not_automatically_repeated(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
) -> None:
    harness = make_dialog()
    dialog = harness.dialog
    assert harness.diagnostics.create_calls == 0
    assert child(dialog, QLabel, "supportBundleSummary").text() == (
        "No support bundle has been created in this dialog."
    )

    child(dialog, QPushButton, "createSupportBundleButton").click()
    wait_until(qapplication, lambda: not dialog.operation_in_progress)

    assert harness.diagnostics.create_calls == 1
    summary = child(dialog, QLabel, "supportBundleSummary").text()
    assert harness.diagnostics.record.filename in summary
    assert "2026-08-02 10:15:30 UTC" in summary
    assert "Members: 6" in summary
    assert "3.0 KiB" in summary
    assert str(BUNDLE_ID) in summary
    assert str(harness.diagnostics.record.path.parent) not in summary
    assert harness.diagnostics.record.archive_sha256 not in summary
    assert messages.information[-1][0] == "Support bundle created"
    assert harness.diagnostics.create_calls == 1


def test_support_error_is_fixed_and_does_not_retry(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
) -> None:
    harness = make_dialog()
    harness.diagnostics.error = SupportBundleCreationError("C:\\private\\logs bearer-token")

    child(harness.dialog, QPushButton, "createSupportBundleButton").click()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)

    assert harness.diagnostics.create_calls == 1
    assert messages.warnings[-1] == (
        "Support bundle not created",
        "The privacy-safe support bundle could not be created and verified.",
    )
    assert "private" not in visible_text(harness.dialog)


def test_support_busy_state_prevents_duplicates_and_blocks_close(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    qapplication: QApplication,
) -> None:
    harness = make_dialog()
    started = Event()
    release = Event()
    harness.diagnostics.started = started
    harness.diagnostics.release = release
    button = child(harness.dialog, QPushButton, "createSupportBundleButton")

    button.click()
    wait_until(qapplication, started.is_set)
    assert not button.isEnabled()
    assert QApplication.overrideCursor() is not None
    button.click()
    harness.dialog._create_support_bundle()
    harness.dialog.setResult(QDialog.DialogCode.Accepted)
    harness.dialog.reject()
    assert harness.dialog.result() == QDialog.DialogCode.Accepted
    assert harness.diagnostics.create_calls == 1

    release.set()
    wait_until(qapplication, lambda: not harness.dialog.operation_in_progress)
    wait_until(qapplication, lambda: harness.dialog.running_worker_count == 0)
    assert harness.diagnostics.create_calls == 1
    assert button.isEnabled()
    assert QApplication.overrideCursor() is None


def test_initial_read_errors_disable_unsafe_restore_and_show_fixed_status(
    qapplication: QApplication,
    tmp_path: Path,
    messages: MessageRecorder,
) -> None:
    preferences = FakePreferencesService(preference_snapshot())
    created = backup_record(tmp_path)
    backups = FakeBackupService(BackupListing((), ()), created)
    restores = FakeRestoreService()
    diagnostics = FakeDiagnosticsService(support_record(tmp_path))
    backups.list_error = BackupVerificationError("D:\\secret\\bad")
    restores.get_error = RestoreStagingError("sqlite:///D:/secret")

    dialog = SettingsRecoveryDialog(
        cast(object, preferences),
        cast(object, backups),
        cast(object, restores),
        cast(object, diagnostics),
    )
    wait_until(qapplication, lambda: not dialog.operation_in_progress)

    assert messages.warnings[-1][0] == "Recovery status unavailable"
    assert "secret" not in messages.warnings[-1][1]
    assert not child(dialog, QPushButton, "stageRestoreFileButton").isEnabled()
    assert "could not be loaded" in child(dialog, QLabel, "pendingRestoreStatus").text()
    dialog.close()
    dialog.deleteLater()
    qapplication.processEvents()


def test_source_boundaries_and_formatting_helpers_are_non_financial() -> None:
    source = Path(dialog_module.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "app.infrastructure",
        "app.domain",
        "sqlalchemy",
        "sqlite3",
        "from alembic",
        "DatabaseManager",
        "UnitOfWork",
        "database_url",
        "subprocess",
        "threading.Thread",
        "float(",
        "Decimal",
        "auto_backup",
        "auto_snapshot",
    ):
        assert forbidden not in source
    assert dialog_module._format_size(0) == "0 B"
    assert dialog_module._format_size(1024) == "1.0 KiB"
    assert dialog_module._format_size(5 * 1024 * 1024) == "5.0 MiB"
    assert dialog_module._format_utc(NOW) == "2026-08-02 10:15:30 UTC"
    with pytest.raises(ValueError):
        dialog_module._format_size(-1)


def test_unsafe_contract_filenames_and_revision_codes_are_not_rendered(
    make_dialog: DialogFactory,
    messages: MessageRecorder,
    tmp_path: Path,
) -> None:
    unsafe = replace(
        backup_record(tmp_path, "safe.mirabackup"),
        filename="line\nbreak.mirabackup",
        alembic_revision="revision with D:\\secret",
    )
    harness = make_dialog(listing=BackupListing((unsafe,), ()))
    text = visible_text(harness.dialog)

    assert "line\nbreak" not in text
    assert "D:\\secret" not in text
    assert "Verified backup" in text
    assert "Unavailable" in text
