---
name: drivekeeper
description: Safely reorganise messy drives, partitions or big folders (Downloads, phone backups, old disks) without losing anything. Surveys without dumping file lists, plans a target layout with the user, confirms duplicates by md5, never deletes or overwrites (removable items go to a reviewable quarantine), dry-runs every move, then applies with verified cross-drive copies. Use when the user wants to organise, restructure, declutter, dedupe or merge drives/folders, sort phone dumps, or clean Downloads.
---

# Drivekeeper

Reorganise files across one or more drives so that **nothing is ever lost**, every move is planned
and reviewable, and the result is a layout the user can navigate. The engine (`scripts/organise.py`)
does the moving; your job is the survey, the plan, and the judgement.

## Non-negotiable rules

1. **Never delete. Never overwrite.** Anything removable (duplicates, old versions, compressed copies,
   junk, empty folders, Windows-only installers on Linux…) goes to a quarantine folder
   (`_to_be_deleted/from <drive>/<category>/<original path>`) for the user to review and empty.
2. **Duplicates need proof.** Same name + size is only a candidate; confirm with md5. Same name, different
   content → keep both (the engine adds a ` (label)` suffix).
3. **Nothing moves without a dry run.** Dry run → read the CSV for surprises → only then `--apply`.
   Unless the user has explicitly pre-approved autonomous execution, show the summary and wait for "apply".
4. **Every file needs a rule.** `organise.py plan.json --check` must report 0 UNPLANNED files before applying.
5. **Don't flood your context.** Never `ls -R`/`find` a big tree to the screen. Use `scripts/inventory.py`
   (summaries) and write any full listing to a file.
6. **Projects move as one unit.** A project is any folder of sources plus what builds them: `.git`, `pyproject.toml`,
   `package.json`, `Makefile`, `CMakeLists.txt`, `Cargo.toml`, `go.mod`, `*.sln`, a `.tex` with its `.aux`/`.log`,
   an editing project file with its media. Keep everything in it together (`.venv`, `node_modules`, `.env`, build
   outputs); never dedupe or junk-clean inside it. Moving a project breaks virtualenvs and build caches; tell the
   user how to rebuild (see below).
7. **Hidden and app-managed files are the user's call.** Report what `inventory.py hidden` finds (caches,
   thumbnails, a phone's "recently deleted" items, markers) with sizes, and ask. Until the user decides, they move
   with their folder or stay where they are; never surface, restore or quarantine them on your own. Functional ones
   (`.git`, `.venv`, `.obsidian`, `.env`) always stay with their project.
8. **Don't reorganise OS-managed folders:** trash folders (Linux `.Trash-*`, `~/.local/share/Trash`; macOS
   `.Trashes`; Windows `$RECYCLE.BIN`) and `System Volume Information`. Moving them breaks the OS's bookkeeping.
   Report their size; emptying a trash is the user's decision.
9. **Cloud placeholders:** OneDrive / iCloud / Google Drive "on-demand" files are not on disk; moving them
   triggers downloads or breaks sync. Survey them separately and leave synced folders to the sync client.

## Workflow

### 1. Survey (read-only)
Use `python3` on Linux/macOS, `py -3` (or `python`) on Windows. Scripts are stdlib-only.
```bash
python3 scripts/inventory.py drives                      # every volume: size / used / free (decimal GB)
python3 scripts/inventory.py survey <root> --depth 2     # size / count / top extensions per folder
python3 scripts/inventory.py dates <root>                # date ranges in names (phone dumps, exports)
python3 scripts/inventory.py hidden <root>...            # hidden content vs app config (dot-names + Windows hidden attribute)
python3 scripts/inventory.py dups <root>...              # md5-confirmed duplicates → dups.csv
```
Also learn which volumes share a physical disk (Linux `lsblk`, Windows `Get-Partition` / Disk Management) and
the file system of each (NTFS, ext4, btrfs, APFS, exFAT) — it decides where games/code may live and what a
"move" costs.
Drill into big folders one level at a time. Identify: phone dumps (`IMG_/PXL_/VID_…` dates, `-WA` =
WhatsApp), movies vs TV (SxxExx), projects (rule 6), multiple versions of
the same project (compare by content hash, keep the newest, recover files only found in old versions),
installers (`.AppImage/.rpm/.deb/.exe/.iso`), archives that may hold unique content.

### 2. Agree on a target layout
Propose one place per kind of file (a "where does X live" table). Ask about anything ambiguous:
which drive gets what (check free space; games/code prefer ext4/btrfs over NTFS), personal documents,
other people's files ("Not mine/"), what counts as old. A sensible default taxonomy is in
`references/layout.md`. Write the plan as Markdown for the user (trees + rename table + quarantine table +
sizes per drive before/after), iterate until no open points.

### 3. Encode it as a plan file
Translate the agreed layout into `plan.json` (format: `references/plan-format.md`). Order matters:
**the first rule that covers a path wins**, so put specific rules (renames, quarantine of known junk)
before broad ones. Use `"log"` on renames to keep an old→new record. Use `"route": "phone"` for camera /
WhatsApp / screenshot dumps (sorted into `Camera/YYYY/YYYY-MM`). Use `"dedupe"` when you know which copy to
keep. List every source folder in `"scan"` so nothing is forgotten.

### 4. Check, dry run, review
```bash
python3 scripts/organise.py --selftest            # engine sanity check (temp folder)
python3 scripts/organise.py plan.json --check     # must print 0 UNPLANNED
python3 scripts/organise.py plan.json             # dry run → plan-dryrun.csv
```
Read the CSV like a reviewer: kept files landing in quarantine, renamed documents treated as duplicates,
counts that don't match your survey, CLASH lines, data per drive vs free space. The dry run can only guess
duplicates by name+size (it reports an upper bound); md5 is checked on apply.

### 5. Apply and monitor
Run `--apply` in the background for big jobs and keep the machine awake and on mains power. The engine prints
progress every 10 s (files, GB done, current file). Other ways to watch: Linux `cat /proc/<PID>/io`,
`ls -l /proc/<PID>/fd`; Windows Task Manager → Details (add "I/O read/write bytes") or Resource Monitor → Disk;
any OS: free space of each drive. Re-running after an interruption is safe (already-moved sources show as MISSING).

### 6. Verify and hand over
Exit code 0; no `*.part` files left (search for them); MISSING/UNPLANNED = 0; old source folders gone or only quarantined;
free space matches the plan. Give the user: a final map (where everything lives), the quarantine summary,
and follow-ups (rebuild virtualenvs: `rm -rf .venv && uv sync` / `npm install`; fix launchers that point
at old paths; empty quarantine when satisfied).

## Lessons that prevent real damage
See `references/lessons.md`. The short version: record a file's size/hash *before* moving it; never let a
quarantined copy count as the "kept" copy; `7z x -aos` (never `-y`); explicit renames must not be
deduplicated against look-alikes elsewhere; same-drive move = rename (instant), cross-drive = copy + verify +
delete; NTFS on Linux is slow (FUSE) and has no full fsck; btrfs compression means freed space < listed size.
