# ClipVault Released-App Data Migration

Date: 2026-07-28
Status: Approved design

## Objective

Keep a user's clipboard history, images, and settings stable across executable downloads, upgrades, and executable locations. Existing data must never be overwritten or deleted by migration.

## Storage Location

Development mode continues to use `<repository>/data` so the existing local workflow remains unchanged. A frozen executable uses the per-user Windows local application data directory:

```text
%LOCALAPPDATA%\ClipVault\data
```

The resolved path is derived through `LOCALAPPDATA`, with a safe user-home fallback for test and unusual Windows environments.

## One-Time Migration

Before database initialization, a frozen executable checks whether the canonical data directory contains `clipboard.db`.

When the canonical directory is empty, it considers legacy data directories in this order:

1. A `data` directory beside the executable.
2. A `data` directory beside a `release` or `dist` executable parent.

For the first legacy directory containing `clipboard.db`, it copies `clipboard.db`, `config.json`, `images`, and `backups` into the canonical data directory. Database image paths that point inside the legacy image directory are rewritten to the canonical image directory.

Migration is idempotent: once a canonical database exists, no later startup copies, merges, overwrites, or deletes anything. Copy failures leave the source untouched and allow normal startup with the existing canonical directory.

## User Recovery

For the current upgrade, the old database at `E:\clipboard-manager\data\clipboard.db` remains untouched. A new release will migrate it when launched from the existing `release` directory. Users who already ran a downloaded executable will receive the shared canonical store once they install the repaired version; no manual database handling is required for the standard upgrade route.

## Testing

- A frozen executable resolves storage to `LOCALAPPDATA\ClipVault\data`.
- A legacy database, configuration, image, and backup are copied exactly once into an empty canonical directory.
- Image database paths are rewritten after migration.
- A populated canonical directory prevents migration and preserves its existing contents.
- Development mode continues to resolve to the repository data directory.

## Acceptance Criteria

- Downloading or moving `ClipVault.exe` does not start a new empty history for an existing user.
- Existing records, images, settings, and backups are preserved.
- The migration never overwrites an existing canonical data store.
- The repaired release is published as `v1.0.1`, and the README documents the data location.
