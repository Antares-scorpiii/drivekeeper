# Lessons (read only if something goes wrong)

Failure modes the engine guards against, and why. Don't work around these guards.

| Mistake | What goes wrong | Guard |
|---|---|---|
| Treating same name + size as a duplicate | Different files lost | md5 before any duplicate goes to the quarantine |
| Counting a quarantined copy as "the kept one" | The original also ends up in quarantine; both lost when it's emptied | Nothing inside the quarantine is ever a kept copy |
| Overwriting on move or extraction (`7z x -y`) | Silent data loss | Never overwrite: a clash keeps both with a ` (drive)` suffix; `7z x -aos` |
| Deleting a source before the copy is checked | A cut-short copy loses the file | Cross-drive: copy to `.part`, md5-verify, rename, then remove the source |
| Two rules for one file | It lands in the wrong place | The first rule wins; specific rules before broad ones |
| Assuming the listing is complete | Forgotten files | `--check`/`--go` stop on anything without a rule |
| Splitting projects, programs or sets | Code, games or albums stop working | Move units whole; keep companion files together |

Facts that change plans:
- Same filesystem: a move is an instant rename. Different filesystems: a full copy; check free space and order steps
  so a drive never fills (move data off a drive before moving data onto it).
- NTFS read from Linux is slow (FUSE); Windows and macOS file systems are case-insensitive (`Photo.jpg` = `photo.jpg`).
- Compressed file systems (btrfs, APFS) free less space than the listed sizes.
- Virtualenvs and build caches contain absolute paths: rebuild them after a project moves. Git repos survive moves.
- Cloud "on-demand" files (OneDrive, iCloud) are not on disk: moving them triggers downloads or breaks sync.
- Windows paths in `plan.json`: `C:/Users/me/...` (forward slashes). Paths over 260 characters may fail on Windows.
