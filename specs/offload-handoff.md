# Offload → Cairn handoff contract

Offload (Mac menu-bar app, this repo) and Cairn (DaVinci Resolve plugin, separate repo)
share no code. They talk only through the two JSON files below. This file is the contract;
an identical copy lives in the other repo (`Cairn/specs/offload-handoff.md` ↔
`Offload/docs/cairn-handoff.md`). Change both together, and bump `schema_version` on any
breaking change.

Cairn must work without Offload. Any shoot folder can be dropped on the panel; Offload
shoots are just recognised and imported in place.

## 1. Shoot manifest — `<shoot>/.cairn/offload.json`

Written by Offload after every file on a card has been copied and read back. Travels with
the footage, so it still works when the drive moves to another Mac.

| Key              | Type           | Notes                                                                         |
| ---------------- | -------------- | ----------------------------------------------------------------------------- |
| `schema_version` | int            | Currently `1`.                                                                |
| `client`         | string         |                                                                               |
| `shoot_name`     | string         |                                                                               |
| `date`           | string         | `yyyy-MM-dd`.                                                                 |
| `camera`         | string         | Camera folder name, e.g. `FX3`.                                               |
| `camera_model`   | string \| null |                                                                               |
| `camera_serial`  | string \| null |                                                                               |
| `card_name`      | string         | Volume name of the card.                                                      |
| `shoot_folder`   | string         | Absolute path.                                                                |
| `footage_folder` | string         | Absolute path.                                                                |
| `photos_folder`  | string \| null | Absolute path.                                                                |
| `copied_at`      | string         | ISO-8601.                                                                     |
| `files`          | array          | `{source, destination, size, sha256}`; source/destination are `file://` URLs. |

A second card into the same shoot merges into `files` (matched on `destination`) rather
than replacing it.

**Layout.** Normal shoots keep footage in `<shoot>/Footage/<Camera>/`. A "Choose folder…"
copy has no `Footage/` folder; Cairn then uses `footage_folder` + `camera`.

**Cairn reads:** the presence of this file (= Offload shoot, import in place, no
rename/moves), `client`, `shoot_name`, `camera`, `date`, `footage_folder`, `files` (count).
Cairn writes its own clip sidecars into the same `.cairn/` folder.

## 2. Inbox — `~/Library/Application Support/Cairn/offload-inbox.json`

A JSON array of shoots Cairn hasn't sorted yet:

```json
[
  {
    "shoot_folder": "/Volumes/…/261015 harvest dinner",
    "manifest": "/Volumes/…/.cairn/offload.json",
    "added_at": "2026-10-15T09:12:00Z"
  }
]
```

- **Offload** appends (or refreshes) an entry after each copy, and rewrites paths when it
  renames a shoot folder. Writes are atomic.
- **Cairn** lists the entries in its panel, marks one unavailable if its manifest can't be
  read (drive unplugged), and removes the entry once the shoot is sorted.

Known gap: both sides do read-modify-write without a lock. A simultaneous write can drop an
entry; re-copying the card re-adds it.

## Offload-only files

`<shoot>/.cairn/sort-log.json` is Offload's own record of a Sort. Cairn doesn't read it.
