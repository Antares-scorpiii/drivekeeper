# Lessons from a real 330 GB, 150k-file reorganisation

Bugs that were caught before they did damage, and what to do instead:

| Mistake | Effect it would have had | Rule |
|---|---|---|
| Reading a file's size *after* moving it | real run crashes on the first confirmed duplicate | record size/hash before moving |
| `7z x -y` | silently overwrites existing files | `7z x -aos` + size check after extraction |
| A file just quarantined is indexed as a "kept" copy | the real original (dozens of notes and guides, an ID photo) also goes to quarantine — and is lost when the quarantine is emptied | never index anything inside the quarantine |
| Explicit renames deduplicated against look-alikes elsewhere | an ID photo lands in quarantine because a copy exists in a phone folder | renames only compare with their own destination |
| Two rules for one file | junk (`desktop.ini`) ends up in a photo folder | first rule wins; put specific rules first |
| Dry run counting name+size matches as duplicates | over-reporting | treat dry-run duplicates as an upper bound; md5 on apply |
| Self-test that never ran the duplicate path | the crash above slipped through | test every code path, including dedupe |
| Assuming a folder listing is complete | forgotten files (new Desktop files, loose videos next to a WhatsApp folder, an album inside a `.7z`) | `--check` for UNPLANNED before every run |

Facts worth knowing:
- `df -h`/file managers show GiB; plan in bytes (`df -B1`).
- btrfs compresses: deleting 14 GB of listed files may free only ~4 GB.
- Same filesystem move = rename (instant, no space). Cross-filesystem = full copy; check free space at every step and order steps so a drive never runs out (move data *off* a drive before moving data *onto* it).
- NTFS on Linux runs through `mount.ntfs` (FUSE): slow, CPU-heavy, and an unclean unmount can leave it read-only (`ntfsfix` clears the flag only).
- Consumer QLC/TLC SSDs slow down on long writes when their cache fills; hours, not minutes, for hundreds of GB. Wear from a few hundred GB is well under 1% of rated endurance; reads don't wear.
- Virtualenvs and build caches (`.venv`, `.next`) contain absolute paths: rebuild after moving. Git repos survive moves.
- Android dumps contain `.thumbnails/` (a cache), `.trashed-<ts>-name` (the phone's "recently deleted"), `.nomedia` and 0-byte `.pending-*` markers. What to do with each is the user's decision: report them, don't guess.
- Files named like lines of code (`for row in table.rows`) come from a multi-line paste run as a shell command; files named `C:\Users\…\x.py` come from extracting a Windows zip. Both are junk.
- Package caches grow silently: `uv cache clean`, `pip cache purge`, `npm cache clean --force` can free tens of GB — rebuild moved venvs first.
- Watch a background job: `/proc/<PID>/io`, `/proc/<PID>/fd`; `pgrep -af '^python3 .*script'` (anchor, or pgrep matches its own shell).

Windows / macOS specifics:
- Paths in `plan.json`: write `C:/Users/me/...` (forward slashes) or escape backslashes. Drive letters are separate
  volumes: moving C: → D: is always a full copy.
- Names illegal on Windows (`: * ? " < > |`, trailing space/dot) and the 260-character path limit: files coming from
  Linux/macOS disks may fail to copy. Enable long paths (`LongPathsEnabled`) or shorten folder names first.
- NTFS and APFS (default) are case-insensitive: `Photo.jpg` and `photo.jpg` collide in one folder → the engine keeps
  both with a suffix.
- Windows Defender / antivirus scans every copied file: expect slower copies; never disable it, just budget time.
- `desktop.ini`, `Thumbs.db` (Windows) and `.DS_Store`, `._*` (macOS) are junk outside system folders.
- Symlinks need Developer Mode/admin on Windows; the engine falls back to writing `<name>.symlink.txt`.
- OneDrive "Files On-Demand"/iCloud placeholders are cloud-only: do not mass-move them.
