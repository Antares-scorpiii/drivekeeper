# plan.json format

```json
{
  "quarantine": "/mnt/data2/_to_be_deleted",
  "labels": {"/mnt/data1": "data1", "/mnt/data2": "data2", "/home/me": "home"},
  "scan": ["/home/me/Downloads", "/mnt/data2/old phone", "/mnt/data2/misc"],
  "logs": {"Personal": "/mnt/data1/Personal/Rename log.csv", "Books": "/mnt/data1/Books/Rename log.csv"},
  "rules": [ ... ]
}
```

| Key | Meaning |
|---|---|
| `quarantine` | Folder that receives everything removable. Layout: `from <label>/<category>/<path relative to that drive>` |
| `labels` | Path prefix → short name. Used for quarantine folders and clash suffixes (`photo (data2).jpg`) |
| `scan` | Source folders that must be fully covered by rules. Files with no rule are reported as UNPLANNED and left alone. Folders in `scan` that end up with no files are moved to `quarantine/…/Empty folders` |
| `logs` | Rename-log name → CSV path. Rules with `"log": "<name>"` append `old path, new path, date` there |
| `log_dir` | Optional. Where `Drivekeeper move log <date>.csv` is written on `--apply` (default: the folder holding the quarantine). `organise.py --undo <log>` reverses it |

## Rules (processed top to bottom; the first rule covering a path wins)

| Rule | Example | Effect |
|---|---|---|
| move into folder | `{"move": "/home/me/Downloads/*.AppImage", "to": "/mnt/data1/Software/"}` | trailing `/` = keep the name, put it in that folder. Globs allowed (`**` recursive) |
| move/rename | `{"move": "/mnt/d/x/pan.pdf", "to": "/mnt/d/Personal/PAN Card.pdf", "log": "Personal"}` | exact destination path; `log` records the rename |
| each | `{"each": "/mnt/d/Phone/WhatsApp", "to": "/home/me/Pictures/Phone/WhatsApp/Images/"}` | every non-hidden entry of the folder (add `"hidden": true` to include hidden) |
| phone routing | `{"route": "phone", "from": "/mnt/d/PhoneDump", "to": "/home/me/Pictures/Phone"}` | recursive, by file name: `Screenshot*`→Screenshots, `*-WA0…`→WhatsApp/Images or Video, `Snapchat*`→Snapchat, a date in the name→`Camera/YYYY/YYYY-MM`, else `Camera/undated`. Hidden files (`.thumbnails/`, `.trashed-*`, markers) are skipped and reported as UNPLANNED: cover them with explicit rules once the user has decided |
| quarantine | `{"quarantine": "/home/me/Downloads/app-v1.AppImage", "category": "Old versions"}` | categories used so far: Duplicates, Old versions, Compressed copies, Windows exe, Junk, Old resumes, Empty folders, Review |
| dedupe | `{"dedupe": "/home/me/Downloads/cert.pdf", "keep": "/home/me/Documents/cert.pdf", "else": "/mnt/d/Certificates/"}` | md5-equal to `keep` (even if `keep` was already moved) → quarantine/Duplicates; otherwise moved to `else` |
| extract | `{"extract": "/mnt/d/album.7z", "to": "/home/me/Pictures/Albums/"}` | 7z/zip extracted with `-aos` (never overwrite), every file size-checked, then the archive is quarantined |

Folders: a `move` of a folder to a path that doesn't exist on the same filesystem is an instant rename; otherwise
its files are moved one by one (merged), keeping the inner structure, symlinks and empty subfolders.

Automatic everywhere: same name + size + md5 as a file already placed outside the quarantine → quarantine/Duplicates
(not for rules with `log`, which only compare with their own destination).
