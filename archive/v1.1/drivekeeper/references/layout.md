# Default target layout (a starting point: adapt it and ask the user)

One place per kind of file. Put big, write-once media on the largest drive; games and code on a Linux
filesystem (ext4/btrfs), not NTFS; the quarantine on the drive with the most free space. Use the OS's own
folder names (Pictures, Videos, Music, Documents, Downloads) where they exist.

```
<home or media drive>/
├── Pictures/
│   ├── Phone/              photos + videos from phones: Camera/YYYY/YYYY-MM/, WhatsApp/, Screenshots/, …
│   ├── Albums/<event>/     named collections
│   └── …                   anything else the survey turns up (screenshots, wallpapers, scans): ask
├── Videos/                 Movies/<Title (Year)>/, TV/<Series>/Season NN/, Clips/
├── Music/<Artist>/<Album>/
└── Documents/Projects/     code, each project moved whole

<data drive>/
├── Personal/               IDs, certificates, education, work, finance, health: subfolders agreed with the user
├── Documents/              manuals, notes, exports/backups, misc
├── Books/<topic>/
├── Games/  Software/       installers by platform
└── _to_be_deleted/from <drive>/<category>/…
```

Naming: only rename when the user asks for it, and log every rename (`"log"` in the plan). A neutral pattern
that reads well: `Title - Author.pdf`, `<Issuer> - <Document> <YYYY-MM>.pdf`, `Lecture 01.pdf`.
Older versions of the same document (resumes, drafts) and compressed copies (`-min`, `_compressed`) go to the
quarantine only when the newest/original is kept, and only after the user agrees which one that is.
