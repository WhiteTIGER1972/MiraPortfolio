# Mira Portfolio internal Alpha testing

This is an internal pre-release build for 64-bit Windows. It is not an installer,
is not code-signed, and is intended only for invited internal Alpha testing.

## Start the application

Extract the entire ZIP archive to a local folder before running it. Do not run
`MiraPortfolio.exe` from inside the ZIP. Keep the `_internal` folder beside
`MiraPortfolio.exe`; the application will not start without its bundled resources.

Double-click `MiraPortfolio.exe` to start. Because this internal build is unsigned,
Windows may show a security warning. Confirm that the filename is
`MiraPortfolio.exe` and proceed only if the ZIP came through the expected internal
testing channel. Close the application normally with the window's Close button.

Mira Portfolio keeps its database, preferences, logs, backups, restore workspace,
exports, and support bundles in its per-user Windows application-data area. It does
not store these files beside the executable. The application does not automatically
upload data and does not automatically check for or install updates.

## Backup, restore, and support

Open **Settings & recovery** from the main toolbar.

- On **Backup & Restore**, choose **Create backup** to make a manual backup.
- Select a verified backup and choose **Stage selected backup**, or choose
  **Stage backup file…**. A staged restore is applied on the next startup that you
  manually initiate. Use **Cancel pending restore** before closing if you do not
  want the staged restore to apply.
- On **Support**, choose **Create support bundle** when an issue needs diagnostic
  context. The application creates the bundle locally; it does not upload it.

If a fatal-error dialog appears, copy its incident reference. Report the issue with
that reference, a short description of what happened, and the locally created
support bundle. Do not include financial details that are not needed to reproduce
the issue.

## Known limitations

- Internal Alpha status; do not treat this build as production-ready.
- Dark theme only.
- A changed log-level preference takes effect after restart.
- No installer and no automatic updater.
- No automatic backup scheduling; create backups manually.
