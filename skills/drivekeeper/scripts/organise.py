#!/usr/bin/env python3
"""Plan-driven, never-lose-anything file organiser.

  organise.py plan.json             dry run (default): changes nothing, writes <plan>-dryrun.csv
  organise.py plan.json --check     only lists UNPLANNED files (no rule covers them)
  organise.py plan.json --go        pre-approved jobs, one call: stop if anything is UNPLANNED, else apply, tidy the
                                    folders this run emptied, verify against the move log, print a short report
  organise.py --verify LOG.csv      every moved file is at its destination with its size, no source or .part left
  organise.py plan.json --apply     does it, then re-points symlinks/launchers whose target moved; writes the move
                                    log next to the quarantine ("Drivekeeper move log <date>.csv", + rename logs)
  organise.py --undo LOG.csv        moves everything in a move log back (newest first), restores links
  organise.py --selftest            tests the engine on a throw-away folder

Plan (JSON):
{
  "quarantine": "/mnt/b/_to_be_deleted",              # where everything "removable" goes (never deleted)
  "labels": {"/mnt/a": "driveA", "/home/me": "home"}, # origin names for quarantine/from <label>/ and clash suffixes
  "scan":   ["/mnt/a/old stuff", "/home/me/Downloads"],# folders that must end up fully covered by rules
  "logs":   {"Personal": "/mnt/a/Personal/Rename log.csv"},
  "rules": [                                          # processed in order; the FIRST rule for a path wins
    {"move": "/home/me/Downloads/*.AppImage", "to": "/mnt/a/Software/"},       # trailing / = into folder
    {"move": "/mnt/b/docs/pan.pdf", "to": "/mnt/a/Personal/PAN Card.pdf", "log": "Personal"},
    {"each": "/mnt/b/Phone/WhatsApp", "to": "/home/me/Pictures/Phone/WhatsApp/Images/"},
    {"route": "phone", "from": "/mnt/b/Phone", "to": "/home/me/Pictures/Phone"},
    {"quarantine": "/home/me/Downloads/old-v1/", "category": "Old versions"},
    {"dedupe": "/home/me/Downloads/a.pdf", "keep": "/mnt/a/Docs/a.pdf", "else": "/mnt/a/Docs/"},
    {"extract": "/mnt/b/album.7z", "to": "/home/me/Pictures/Albums/"},
    {"keep": "/mnt/b/Phone/.thumbnails"},                                       # leave in place, on purpose
    {"sort": "/mnt/b/old laptop", "to": {"photo": "/home/me/Pictures/Old laptop", "document": "/mnt/a/Documents/Old laptop",
                                         "project": "/home/me/Projects/", "other": "/mnt/a/Misc/Old laptop"}}
  ]
}
Works on Linux, macOS and Windows (Python 3.8+, stdlib only). On Windows write paths as "C:/Users/me/..."
(forward slashes) or escaped backslashes in JSON. .zip/.tar archives are extracted with Python; .7z/.rar need 7z.

Guarantees: never overwrites (a different file at the destination -> " (label)" suffix); never deletes except the
source of a verified cross-drive copy; cross-drive copies go to .part, are md5-verified, then renamed; duplicates
are confirmed by md5 before quarantining; nothing inside the quarantine counts as a "kept" copy; explicit renames
(rules with "log") only compare against their own destination; safe to re-run after an interruption.
"""
import collections, csv, glob, hashlib, json, os, re, shutil, subprocess, sys, tarfile, tempfile, time, zipfile

SEP = os.sep
norm = os.path.normpath

RESERVE = 2 * 10**9                       # keep this much free on a destination filesystem
DATE = re.compile(r'(?<!\d)(19[89]\d|20[0-4]\d)[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])(?!\d{3})')
VIDEO = ('.mp4', '.mov', '.3gp', '.mkv', '.avi', '.webm', '.m4v')
KINDS = {k: tuple(v.split()) for k, v in {                     # "sort" rule: file kind by extension
    'photo': '.jpg .jpeg .png .gif .heic .heif .webp .tif .tiff .bmp .cr2 .cr3 .nef .arw .dng .orf .rw2 .raf',
    'video': '.mp4 .mov .mkv .avi .wmv .3gp .m4v .webm .mpg .mpeg .rm .flv .mts .vob',
    'music': '.mp3 .flac .wav .m4a .aac .ogg .wma .opus .mid .midi .aiff .amr',
    'document': '.pdf .doc .docx .odt .rtf .txt .md .xls .xlsx .ods .csv .ppt .pptx .pps .ppsx .odp .wps .pages .numbers .key .html .htm',
    'book': '.epub .mobi .azw .azw3 .djvu .fb2 .cbz .cbr',
    'archive': '.zip .7z .rar .tar .gz .tgz .bz2 .xz .zst .001 .002 .003',
    'software': '.exe .msi .dmg .pkg .deb .rpm .appimage .apk .iso .flatpakref .qcow2 .vdi .vmdk .vhd .vhdx',
    'mail': '.eml .mbox .pst .ost .msg .vcf .ics',
    'design': '.psd .ai .xcf .svg .indd .sketch .fig .blend .stl .obj .ttf .otf .woff .woff2'}.items()}
OS_DIRS = ('.Trash', '$RECYCLE.BIN', 'System Volume Information', '.Trashes', '.Spotlight-V100', '.fseventsd', 'lost+found')
PROJECT = {'.git', 'pyproject.toml', 'package.json', 'setup.py', 'Makefile', 'CMakeLists.txt', 'Cargo.toml', 'go.mod', 'pom.xml',
           'build.gradle', 'composer.json', 'Gemfile'}


def kind(name):
    low = name.lower()
    return next((k for k, exts in KINDS.items() if low.endswith(exts)), 'other')


def is_project(folder, names):
    """Sources plus what builds them (SKILL.md rule 6): move as one unit, never sort inside."""
    exts = [os.path.splitext(n)[1].lower() for n in names]
    return (bool(PROJECT & set(names)) or bool(set(exts) & {'.sln', '.xcodeproj', '.prproj', '.drp'}) or {'.tex', '.aux'} <= set(exts)
            or sum(e in ('.html', '.htm') for e in exts) >= 5)                  # a saved website / site backup


JUNK = re.compile(r'^(desktop\.ini|thumbs\.db|\.ds_store|~\$.*|\.~lock\..*#|.*\.crdownload|.*\.tmp)$', re.I)


def is_program(names):
    """An installed program or game: move whole (its .exe needs its data files next to it)."""
    low = [n.lower() for n in names]
    exts = [os.path.splitext(n)[1] for n in low]
    return ('bin' in low and 'lib' in low) or any(n.startswith('unins') and n.endswith('.exe') for n in low) or (
        '.exe' in exts and (sum(e in ('.pak', '.bin', '.dat', '.dll', '.so', '.jar', '.pck') for e in exts) >= 2 or 'data' in low))


def companions(names):
    """Kind per file, but sets stay together: files sharing a stem (Film.mkv + Film.en.srt, IMG_1.HEIC + IMG_1.MOV,
    x.7z.001/.002) follow their largest member's kind, and in a folder that is mostly music or video the few other
    files (cover art, .cue, .nfo, subtitles) follow the majority."""
    k = {n: kind(n) for n, _ in names}
    stems = {}
    for n, size in names:
        stems.setdefault(n.split('.')[0].lower(), []).append((size, n))
    for group in stems.values():
        if len({k[n] for _, n in group}) > 1:
            lead = k[max(group)[1]]
            for _, n in group:
                k[n] = lead
    size = dict(names)
    weight = {x: sum(size[n] for n in k if k[n] == x) for x in set(k.values())}
    major = max(weight, key=weight.get) if weight else None
    if major in ('music', 'video') and weight[major] >= 0.8 * sum(size.values()) and sum(v != major for v in k.values()) <= 4:
        k = {n: major for n in k}                                   # cover art, .srt, .cue, .nfo stay with the album/film
    return k


class Engine:
    def __init__(self, plan, apply=False):
        self.plan, self.apply = plan, apply
        self.q = norm(plan['quarantine'])
        self.labels = {norm(k): v for k, v in plan.get('labels', {}).items()}
        self.progress_t, self.done_bytes = time.time(), 0
        self.ops, self.claimed, self.log = [], set(), []
        self.index, self.planned, self.moved, self.renames, self.stats = {}, {}, set(), {}, {}
        self.hashes = {}

    # ── helpers ────────────────────────────────────────────────────────────────────────────────
    def label(self, p):
        best = max((r for r in self.labels if p == r or p.startswith(r.rstrip(SEP) + SEP)), key=len, default=None)
        return (best, self.labels[best]) if best else (os.path.dirname(p), 'other')

    def qpath(self, src, cat):
        root, lab = self.label(src)
        return os.path.join(self.q, f'from {lab}', cat, os.path.relpath(src, root))

    def dev(self, p):
        return os.stat(self._existing(p)).st_dev

    def exists(self, p):
        return os.path.lexists(p) or (not self.apply and p in self.planned)

    def note(self, action, src, dst='', size=0, info=''):
        self.log.append((action, src, dst, size, info))
        self.stats[action] = self.stats.get(action, 0) + 1

    def hash(self, p):                                             # md5, read once per path
        if p not in self.hashes:
            self.hashes[p] = self.md5(p)
        return self.hashes[p]

    @staticmethod
    def md5(p):
        h = hashlib.md5()
        with open(p, 'rb') as f:
            for c in iter(lambda: f.read(1 << 20), b''):
                h.update(c)
        return h.hexdigest()

    # ── building the operation list (reads directory listings only) ────────────────────────────
    def claim(self, kind, src, dst=None, extra=None):
        src = norm(src)
        dst = norm(dst) if isinstance(dst, str) else dst
        if not os.path.lexists(src):
            return
        if src in self.claimed:
            self.note('RULE-IGNORED', src, str(dst or extra), 0, 'an earlier rule already covers this path')
            return
        self.claimed.add(src)
        self.ops.append((kind, src, dst, extra))

    @staticmethod
    def target(src, to):
        into = to.endswith(('/', '\\'))
        return norm(os.path.join(to, os.path.basename(norm(src)))) if into else norm(to)

    def phone_dest(self, name, base):
        low = name.lower()
        vid = low.endswith(VIDEO)
        if low.startswith('screenshot'):
            return f'{base}/Screenshots'
        if re.search(r'-wa\d', low):
            return f'{base}/WhatsApp/' + ('Video' if vid else 'Images')
        if low.startswith('snapchat'):
            return f'{base}/Snapchat'
        m = DATE.search(name)
        return f'{base}/Camera/{m[1]}/{m[1]}-{m[2]}' if m else f'{base}/Camera/undated'

    def route_phone(self, folder, base):
        for dp, dn, fn in os.walk(folder):
            dn.sort()
            for f in sorted(fn):
                p = os.path.join(dp, f)
                if f.startswith('.') or f.lower() in ('desktop.ini', 'thumbs.db'):
                    continue                                       # hidden (.thumbnails, .trashed-*, markers): the user decides → UNPLANNED / explicit rules
                elif os.path.getsize(p) > 0:
                    self.claim('move', p, os.path.join(self.phone_dest(f, base), f))

    def covered(self, p):
        while True:
            if p in self.claimed:
                return True
            parent = os.path.dirname(p)
            if parent == p or not parent:
                return False
            p = parent

    def sort_folder(self, folder, to, keep_folders=True, junk=True):
        """Route every file under folder by kind into to[kind] (or to['other']); projects move whole to to['project'],
        installed programs/games whole to to['app'] (or to['software']); well-known junk goes to quarantine/Junk.
        Hidden files and kinds with no destination are left for UNPLANNED, so nothing is guessed silently."""
        for dp, dn, fn in os.walk(folder):
            unit = 'project' if dp != folder and is_project(dp, dn + fn) else 'app' if dp != folder and is_program(dn + fn) else None
            if unit:
                dn[:] = []
                dest = to.get(unit) or (to.get('software') if unit == 'app' else None)
                if dest and not self.covered(dp):
                    self.claim('move', dp, self.target(dp, dest if dest.endswith(('/', '\\')) else dest + SEP))
                continue
            dn[:] = sorted(d for d in dn if not d.startswith('.') and not d.startswith(OS_DIRS) and not self.covered(os.path.join(dp, d)))
            visible = sorted(f for f in fn if not f.startswith('.'))
            kinds = companions([(f, os.lstat(os.path.join(dp, f)).st_size) for f in visible])
            for f in visible:
                p = os.path.join(dp, f)
                if self.covered(p):
                    continue
                if junk and not os.path.islink(p) and (JUNK.match(f) or os.path.getsize(p) == 0):
                    self.claim('move', p, self.qpath(p, 'Junk'))
                    continue
                dest = to.get(kinds[f], to.get('other'))
                if not dest:
                    continue
                rel = os.path.relpath(dp, folder) if keep_folders else '.'
                self.claim('move', p, norm(os.path.join(dest, rel, f)))

    def build(self):
        for r in self.plan['rules']:
            if 'route' in r:
                if os.path.isdir(r['from']):
                    self.route_phone(norm(r['from']), norm(r['to']))
                continue
            if 'sort' in r:
                for src in sorted(glob.glob(norm(r['sort']))):
                    if os.path.isdir(src):
                        self.sort_folder(src, r['to'], r.get('keep_folders', True), r.get('junk', True))   # raw: a trailing / means "into"
                continue
            key = next(k for k in ('move', 'each', 'quarantine', 'dedupe', 'extract', 'keep') if k in r)
            pat = r[key]
            hits = sorted(glob.glob(norm(pat), recursive=True)) if any(c in pat for c in '*?[') else [norm(pat)]
            for src in hits:
                if key == 'move':
                    self.claim('move', src, self.target(src, r['to']), r.get('log'))
                elif key == 'each':
                    if os.path.isdir(src):
                        for e in sorted(os.listdir(src)):
                            if r.get('hidden') or not e.startswith('.'):
                                self.claim('move', os.path.join(src, e), norm(os.path.join(r['to'], e)))
                elif key == 'quarantine':
                    self.claim('move', src, self.qpath(src, r.get('category', 'Review')))
                elif key == 'dedupe':
                    self.claim('dedupe', src, self.target(src, r['else']), norm(r['keep']))
                elif key == 'extract':
                    self.claim('extract', src, norm(r['to']))
                elif key == 'keep':
                    self.claim('keep', src)

    def unplanned(self):
        out = []
        for root in map(norm, self.plan.get('scan', [])):
            for dp, dn, fn in os.walk(root):
                dn[:] = [d for d in dn if not d.startswith(OS_DIRS)]          # OS folders are never reorganised
                if dp in self.claimed:
                    dn[:] = []
                    continue
                out += [os.path.join(dp, f) for f in fn + [d for d in dn if os.path.islink(os.path.join(dp, d))]
                        if not self.covered(os.path.join(dp, f))]                    # folder symlinks need a rule too
        return out

    # ── execution ──────────────────────────────────────────────────────────────────────────────
    def check_space(self, dst, size):
        if self.apply and size and shutil.disk_usage(self._existing(dst)).free - size < RESERVE:
            raise SystemExit(f'STOP: not enough space for {dst} ({size/1e9:.1f} GB)')

    @staticmethod
    def _existing(p):
        while not os.path.exists(p):
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        return p

    def suffixed(self, dst, src):
        base, ext = os.path.splitext(dst)
        tag = ' (' + self.label(src)[1] + ')'
        cand, i = f'{base}{tag}{ext}', 2
        while self.exists(cand):
            cand, i = f'{base}{tag} {i}{ext}', i + 1
        return cand

    def copy_verified(self, src, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.islink(src):
            try:
                os.symlink(os.readlink(src), dst)
            except OSError:
                with open(dst + '.symlink.txt', 'w') as f:
                    f.write(os.readlink(src))
            os.unlink(src)
            return
        part, h = dst + '.part', hashlib.md5()
        with open(src, 'rb') as fi, open(part, 'wb') as fo:
            for c in iter(lambda: fi.read(1 << 20), b''):
                h.update(c)
                fo.write(c)
            fo.flush()
            os.fsync(fo.fileno())
        if self.md5(part) != h.hexdigest():
            os.unlink(part)
            raise SystemExit(f'STOP: verify failed copying {src}')
        try:
            shutil.copystat(src, part)
        except OSError:
            st = os.stat(src)
            os.utime(part, (st.st_atime, st.st_mtime))
        os.rename(part, dst)
        os.unlink(src)

    def place_file(self, src, dst, log=None, allow_dup=True):
        if not os.path.lexists(src):
            return self.note('MISSING', src, dst)
        size = os.lstat(src).st_size
        if allow_dup and size and not os.path.islink(src):
            # content, not name: any file already placed with the same size and md5 is the kept copy. Explicit renames
            # (rules with "log") only compare against their own destination.
            cands = ([dst] if self.exists(dst) else []) + ([] if log else self.index.get(size, []))
            for other in cands:
                real = other if self.apply else self.planned.get(other, other)
                if real != src and os.path.isfile(real) and os.path.getsize(real) == size and self.hash(src) == self.hash(real):
                    self.note('DUPLICATE', src, other, size, 'md5-equal')
                    return self.place_file(src, self.qpath(src, 'Duplicates'), None, allow_dup=False)
        if self.exists(dst):
            new = self.suffixed(dst, src)
            self.note('CLASH-KEPT-BOTH', src, new, size, f'a different file is already at {dst}')
            dst = new
        same = self.dev(src) == self.dev(dst)
        if not same:
            self.check_space(dst, size)
        if self.apply:
            if same:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(src, dst)
            else:
                self.copy_verified(src, dst)
        else:
            self.planned[dst] = src
        if size and not dst.startswith(self.q + SEP):              # never treat a quarantined copy as "kept"
            self.index.setdefault(size, []).append(dst)
        self.moved.add(src)
        self.note('MOVE' if same else 'COPY+VERIFY+REMOVE', src, dst, size)
        self.done_bytes += size
        if self.apply and time.time() - self.progress_t > 10:
            self.progress_t = time.time()
            print(f'  … {len(self.moved)} files, {self.done_bytes/1e9:.2f} GB done — now: {dst}', file=sys.stderr, flush=True)
        if log:
            self.renames.setdefault(log, []).append((src, dst))
        return dst

    def place(self, src, dst, log=None):
        if not os.path.lexists(src):
            return self.note('MISSING', src, dst)
        if not os.path.isdir(src) or os.path.islink(src):
            return self.place_file(src, dst, log)
        if not self.exists(dst) and self.dev(src) == self.dev(dst):  # whole-folder rename: instant
            if self.apply:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(src, dst)
            else:
                self.planned[dst] = src
            self.moved.add(src)
            return self.note('MOVE-FOLDER', src, dst)
        for dp, dn, fn in os.walk(src):
            target = os.path.normpath(os.path.join(dst, os.path.relpath(dp, src)))
            if self.apply:
                os.makedirs(target, exist_ok=True)
            for f in fn + [d for d in dn if os.path.islink(os.path.join(dp, d))]:
                if os.path.join(dp, f) not in self.moved:
                    self.place_file(os.path.join(dp, f), os.path.join(target, f), log, allow_dup=False)
        if self.apply:
            for dp, _, _ in os.walk(src, topdown=False):
                try:
                    os.rmdir(dp)                                    # only the now-empty folders we emptied
                except OSError:
                    pass

    def run_op(self, op):
        kind, src, dst, extra = op
        if kind == 'keep':
            return self.note('KEPT', src, '', 0, 'a "keep" rule leaves it where it is')
        if kind == 'move':
            self.place(src, dst, extra)
        elif kind == 'dedupe':
            if not os.path.lexists(src):
                return self.note('MISSING', src, dst)
            keep = extra if os.path.exists(extra) else (self.index.get((os.path.basename(extra), os.path.getsize(src)))
                                                         if self.apply else next((d for d, s in self.planned.items() if s == extra), None))
            if keep and (not self.apply or self.md5(src) == self.md5(keep)):
                self.note('DUPLICATE', src, extra, os.path.getsize(src), 'md5-equal' if self.apply else 'md5 checked on --apply')
                self.place_file(src, self.qpath(src, 'Duplicates'), None, allow_dup=False)
            else:
                self.place(src, dst)
        elif kind == 'extract':
            files = self.archive_listing(src)
            if self.apply:
                self.check_space(dst, sum(files.values()))
                self.extract_safe(src, dst)
                bad = [p for p, s in files.items() if not os.path.isfile(os.path.join(dst, p)) or os.path.getsize(os.path.join(dst, p)) != s]
                if bad:
                    raise SystemExit(f'STOP: extraction check failed for {src}: {bad[:3]}')
            self.note('EXTRACT', src, dst, sum(files.values()), f'{len(files)} files')
            self.place_file(src, self.qpath(src, 'Extracted archives'), None, allow_dup=False)

    @staticmethod
    def archive_listing(src):
        if zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z:
                return {i.filename: i.file_size for i in z.infolist() if not i.is_dir()}
        if tarfile.is_tarfile(src):
            with tarfile.open(src) as t:
                return {m.name: m.size for m in t.getmembers() if m.isfile()}
        seven = shutil.which('7z') or shutil.which('7z.exe') or shutil.which('7za')
        if not seven:
            raise SystemExit(f'STOP: {src} needs 7-Zip (7z) to list/extract')
        out = subprocess.run([seven, 'l', '-slt', src], capture_output=True, text=True).stdout
        files = {}
        for block in out.split('\n\n'):
            kv = dict(l.split(' = ', 1) for l in block.splitlines() if ' = ' in l)
            if 'Size' in kv and 'Attributes' in kv and not kv['Attributes'].startswith('D') and kv.get('Path') != src:
                files[kv['Path']] = int(kv['Size'])
        return files

    @staticmethod
    def extract_safe(src, dst):
        """Extract without ever overwriting an existing file and without escaping dst."""
        root = os.path.realpath(dst)
        def safe(name):
            p = os.path.realpath(os.path.join(root, name))
            if not (p == root or p.startswith(root + SEP)):
                raise SystemExit(f'STOP: archive member escapes the target folder: {name}')
            return p
        if zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z:
                for i in z.infolist():
                    p = safe(i.filename)
                    if i.is_dir() or os.path.exists(p):
                        continue
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with z.open(i) as fi, open(p, 'wb') as fo:
                        shutil.copyfileobj(fi, fo)
        elif tarfile.is_tarfile(src):
            with tarfile.open(src) as t:
                for m in t.getmembers():
                    p = safe(m.name)
                    if m.isfile() and not os.path.exists(p):
                        os.makedirs(os.path.dirname(p), exist_ok=True)
                        with t.extractfile(m) as fi, open(p, 'wb') as fo:
                            shutil.copyfileobj(fi, fo)
        else:
            seven = shutil.which('7z') or shutil.which('7z.exe') or shutil.which('7za')
            subprocess.run([seven, 'x', '-aos', '-bso0', '-bsp0', f'-o{dst}', src], check=True)   # -aos: never overwrite

    def relink(self):
        """Re-point symlinks and launchers (.desktop/.url) that point into anything this run moved, and relative
        symlinks that were moved themselves. Logged as RELINK, reversed by --undo."""
        moved = {s: d for a, s, d, *_ in self.log if a in MOVED_ACTIONS}
        if not moved:
            return
        back = {d: s for s, d in moved.items()}
        roots = {norm(r) for r in list(self.labels) + self.plan.get('scan', []) if os.path.isdir(r)}
        for root in [r for r in roots if not any(r != o and r.startswith(o + SEP) for o in roots)]:
            for dp, dn, fn in os.walk(root):
                for x in dn + fn:
                    p = os.path.join(dp, x)
                    if os.path.islink(p):
                        t = os.readlink(p)
                        was_dir = os.path.dirname(follow(p, back))       # where the link lived before this run
                        target = norm(t if os.path.isabs(t) else os.path.join(was_dir, t))
                        final = follow(target, moved)
                        new = final if os.path.isabs(t) else os.path.relpath(final, dp)
                        if new != t:
                            os.unlink(p)
                            os.symlink(new, p)
                            self.note('RELINK', p, new, 0, f'symlink was -> {t}')
                    elif x.lower().endswith(('.desktop', '.url')) and os.path.isfile(p) and rewrite_paths(p, moved):
                        self.note('RELINK', p, '', 0, 'launcher: moved paths updated')

    def drop_emptied(self):
        """Remove folders that this run emptied (they held files it moved); folders that were already empty stay."""
        scan = [norm(r) for r in self.plan.get('scan', [])]
        dirs = {os.path.dirname(s) for a, s, d, *_ in self.log if a in MOVED_ACTIONS}
        for d in sorted(dirs, key=len, reverse=True):
            while any(d.startswith(r + SEP) for r in scan) and d not in scan:
                try:
                    os.rmdir(d)
                except OSError:
                    break
                self.note('REMOVED-EMPTY-FOLDER', d)
                d = os.path.dirname(d)

    def sweep_empty(self):
        for root in map(norm, self.plan.get('scan', [])):
            if os.path.isdir(root) and not any(fn for _, _, fn in os.walk(root)):
                self.place(root, self.qpath(root, 'Empty folders'))

    def run(self, out_prefix):
        self.build()
        for p in self.unplanned():
            self.note('UNPLANNED', p, '', os.lstat(p).st_size, 'no rule covers it: left where it is')
        for op in self.ops:
            self.run_op(op)
        log = out_prefix + '-dryrun.csv'
        if self.apply:
            self.drop_emptied()
            self.sweep_empty()
            self.relink()
            log = os.path.join(self.plan.get('log_dir') or os.path.dirname(self.q),
                               f'Drivekeeper move log {time.strftime("%Y-%m-%d %H%M%S")}.csv')
            os.makedirs(os.path.dirname(log), exist_ok=True)
        self.log_path = log
        with open(log, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['action', 'source', 'destination', 'bytes', 'info'])
            w.writerows(self.log)
        for name, rows in self.renames.items():
            path = self.plan.get('logs', {}).get(name)
            if not path:
                continue
            path = path if self.apply else f'{out_prefix}-preview-{name}-renames.csv'
            os.makedirs(os.path.dirname(path), exist_ok=True)
            new = not os.path.exists(path)
            with open(path, 'a', newline='') as f:
                w = csv.writer(f)
                if new:
                    w.writerow(['old path', 'new path', 'date'])
                w.writerows((a, b, time.strftime('%Y-%m-%d')) for a, b in rows)
        return self.stats


MOVED_ACTIONS = ('MOVE', 'MOVE-FOLDER', 'COPY+VERIFY+REMOVE')


def follow(p, mapping):
    """Where p is after applying mapping to p or its nearest mapped parent folder."""
    q = p
    while True:
        if q in mapping:
            return mapping[q] + p[len(q):]
        parent = os.path.dirname(q)
        if parent == q or not parent:
            return p
        q = parent


def rewrite_paths(path, mapping):
    """Replace whole-path occurrences of mapping keys inside a small text file (launchers). True if changed."""
    try:
        with open(path, encoding='utf-8', errors='surrogateescape') as f:
            txt = f.read(1 << 20)
    except OSError:
        return False
    keys = sorted(mapping, key=len, reverse=True)
    pat = re.compile('(' + '|'.join(map(re.escape, keys)) + r')(?=$|[/\\\s"\'])', re.M)
    new = pat.sub(lambda m: mapping[m.group(1)], txt)
    if new == txt:
        return False
    with open(path, 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write(new)
    return True


def verify(rows, roots=()):
    """Mechanical proof from the move log: each moved file is at its destination with the logged size and its source
    is gone; no half-copied .part file anywhere under roots."""
    problems = []
    for action, src, dst, size, info in rows:
        if action in ('MOVE', 'COPY+VERIFY+REMOVE'):
            if not os.path.lexists(dst):
                problems.append(f'missing at destination: {dst}')
            elif os.path.isfile(dst) and not os.path.islink(dst) and os.path.getsize(dst) != int(size or 0):
                problems.append(f'size changed: {dst}')
        if action in MOVED_ACTIONS and os.path.lexists(src) and not os.path.isdir(src):
            problems.append(f'source still present: {src}')
        if action == 'MOVE-FOLDER' and not os.path.isdir(dst):
            problems.append(f'folder missing at destination: {dst}')
    for r in roots:
        for dp, dn, fn in os.walk(r) if os.path.isdir(r) else ():
            problems += [f'half-copied file: {os.path.join(dp, f)}' for f in fn if f.endswith('.part')]
            problems += [f'broken link: {os.path.join(dp, x)} -> {os.readlink(os.path.join(dp, x))}' for x in dn + fn
                         if os.path.islink(os.path.join(dp, x)) and not os.path.exists(os.path.join(dp, x))]
    return problems


def report(eng):
    """Short summary for the agent's context; the move log holds every line."""
    st, q = eng.stats, eng.q
    files = st.get('MOVE', 0) + st.get('COPY+VERIFY+REMOVE', 0)
    nbytes = sum(int(r[3] or 0) for r in eng.log if r[0] in ('MOVE', 'COPY+VERIFY+REMOVE'))
    quar = collections.Counter(os.path.relpath(d, q).split(SEP)[1] for a, s, d, *_ in eng.log
                               if a in MOVED_ACTIONS and d.startswith(q + SEP) and os.path.relpath(d, q).count(SEP) >= 1)
    dests = {r[2] for r in eng.log if r[0] in MOVED_ACTIONS}
    left = [p for p in eng.unplanned() if not eng.covered(p) and not any(p == d or p.startswith(d + SEP) for d in dests)
            and not p.startswith(q + SEP) and p != eng.log_path]
    roots = sorted({norm(r) for r in list(eng.labels) + eng.plan.get('scan', [])} | {q})
    problems = verify(eng.log, roots)
    out = [f'APPLIED: {files} files ({nbytes / 1e9:.2f} GB): {st.get("MOVE", 0)} renamed in place, '
           f'{st.get("COPY+VERIFY+REMOVE", 0)} copied across drives and md5-verified; {st.get("MOVE-FOLDER", 0)} whole folders',
           f'Quarantine {q}: ' + (', '.join(f'{k} {v}' for k, v in quar.most_common()) or 'nothing'),
           f'Kept both (same name, different content): {st.get("CLASH-KEPT-BOTH", 0)}   duplicates (md5): {st.get("DUPLICATE", 0)}   '
           f'links re-pointed: {st.get("RELINK", 0)}   emptied folders removed: {st.get("REMOVED-EMPTY-FOLDER", 0)}   '
           f'sources missing: {st.get("MISSING", 0)}',
           'VERIFY: ' + ('OK: every moved file is at its destination with its size, no source or .part file left'
                         if not problems else f'{len(problems)} PROBLEMS'), *problems[:15],
           f'Left in place (no rule): {len(left)}', *[f'  {p}' for p in left[:10]],
           f'Move log: {eng.log_path}', f'Undo:     python3 {os.path.abspath(__file__)} --undo "{eng.log_path}"']
    print('\n'.join(out))
    return not problems


def undo(log_path):
    """Reverse a move log: newest move first; never overwrites (an occupied original path is skipped and reported)."""
    with open(log_path, newline='') as f:
        rows = list(csv.reader(f))[1:]
    eng = Engine({'quarantine': os.path.dirname(os.path.abspath(log_path))}, apply=True)
    moved = {r[1]: r[2] for r in rows if r[0] in MOVED_ACTIONS}
    back = {d: s for s, d in moved.items()}
    for action, src, dst, size, info in reversed(rows):
        if action == 'RELINK':
            if os.path.islink(src) and info.startswith('symlink was -> '):
                os.unlink(src)
                os.symlink(info[len('symlink was -> '):], src)
            elif os.path.isfile(src):
                rewrite_paths(src, back)
            eng.note('UNDO-RELINK', src)
        elif action in MOVED_ACTIONS:
            if not os.path.lexists(dst):
                eng.note('UNDO-SKIPPED', dst, src, 0, 'no longer at the destination')
            elif os.path.lexists(src):
                eng.note('UNDO-SKIPPED', dst, src, 0, 'something is already at the original path')
            else:
                os.makedirs(os.path.dirname(src), exist_ok=True)
                if os.path.isdir(dst) and not os.path.islink(dst) or eng.dev(dst) == eng.dev(src):
                    os.rename(dst, src)
                else:
                    eng.copy_verified(dst, src)
                eng.note('UNDO-MOVE', dst, src, int(size or 0))
    out = os.path.splitext(log_path)[0] + ' - undo.csv'
    with open(out, 'w', newline='') as f:
        csv.writer(f).writerows([('action', 'from', 'back to', 'bytes', 'info')] + eng.log)
    return eng.stats, out


def selftest():
    with tempfile.TemporaryDirectory() as d:
        A, B, H, Q = f'{d}/A', f'{d}/B', f'{d}/H', f'{d}/B/_q'
        files = {f'{A}/a/x.jpg': b'1', f'{B}/b/x.jpg': b'1', f'{B}/b/y.jpg': b'2', f'{A}/c/y.jpg': b'3',
                 f'{A}/dir/sub/z.txt': b'4', f'{A}/m/g.pdf': b'gate', f'{B}/books/g.pdf': b'gate',
                 f'{A}/ph/DSC.JPG': b'me', f'{B}/pd/DSC.JPG': b'me', f'{A}/phone/IMG_20240611_1010.jpg': b'p',
                 f'{A}/phone/.trashed-17000-Screenshot_20240716.png': b's', f'{A}/phone/.nomedia': b'',
                 f'{A}/loose.txt': b'?'}
        for p, c in files.items():
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'wb').write(c)
        os.makedirs(f'{A}/dir/empty')
        plan = {'quarantine': Q, 'labels': {A: 'A', B: 'B', H: 'H'}, 'scan': [f'{A}/phone'],
                'logs': {'P': f'{H}/P/Rename log.csv'},
                'rules': [{'move': f'{A}/a/x.jpg', 'to': f'{H}/P/'}, {'move': f'{B}/b/x.jpg', 'to': f'{H}/P/'},
                          {'move': f'{B}/b/y.jpg', 'to': f'{H}/P/'}, {'move': f'{A}/c/y.jpg', 'to': f'{H}/P/'},
                          {'move': f'{A}/dir', 'to': f'{H}/D'},
                          {'dedupe': f'{A}/m/g.pdf', 'keep': f'{B}/books/g.pdf', 'else': f'{H}/x/'},
                          {'move': f'{B}/books/g.pdf', 'to': f'{A}/Books/'},
                          {'move': f'{A}/ph/DSC.JPG', 'to': f'{H}/Phone/'},
                          {'move': f'{B}/pd/DSC.JPG', 'to': f'{A}/PD/Photo.jpg', 'log': 'P'},
                          {'route': 'phone', 'from': f'{A}/phone', 'to': f'{H}/Pictures/Phone'},
                          {'move': f'{A}/a/x.jpg', 'to': f'{H}/elsewhere/'}]}
        e = Engine(plan, apply=True)
        e.run(f'{d}/test')
        assert open(f'{H}/P/x.jpg', 'rb').read() == b'1' and os.path.isfile(f'{Q}/from B/Duplicates/b/x.jpg')
        assert open(f'{H}/P/y (A).jpg', 'rb').read() == b'3' and open(f'{H}/P/y.jpg', 'rb').read() == b'2'
        assert os.path.isfile(f'{H}/D/sub/z.txt') and os.path.isdir(f'{H}/D/empty')
        assert os.path.isfile(f'{A}/Books/g.pdf') and os.path.isfile(f'{Q}/from A/Duplicates/m/g.pdf')
        assert os.path.isfile(f'{A}/PD/Photo.jpg') and os.path.isfile(f'{H}/Phone/DSC.JPG')
        assert os.path.isfile(f'{H}/Pictures/Phone/Camera/2024/2024-06/IMG_20240611_1010.jpg')
        assert os.path.isfile(f'{A}/phone/.trashed-17000-Screenshot_20240716.png')            # hidden: left for the user
        assert e.stats.get('RULE-IGNORED') == 1 and e.stats.get('UNPLANNED') == 2          # .nomedia + .trashed left, reported
        assert os.path.isfile(f'{H}/P/Rename log.csv') and os.path.isfile(f'{A}/loose.txt')
        # archive extraction (python zipfile): extracted + size-checked, archive quarantined, no overwrite
        os.makedirs(f'{B}/alb')
        with zipfile.ZipFile(f'{B}/alb/trip.zip', 'w') as z:
            z.writestr('Trip/IMG_1.jpg', b'one'); z.writestr('Trip/IMG_2.jpg', b'two')
        e2 = Engine({'quarantine': Q, 'labels': {B: 'B', H: 'H'}, 'rules': [{'extract': f'{B}/alb/trip.zip', 'to': f'{H}/Albums/'}]}, apply=True)
        e2.run(f'{d}/test2')
        assert open(os.path.join(H, 'Albums', 'Trip', 'IMG_2.jpg'), 'rb').read() == b'two'
        assert os.path.isfile(os.path.join(Q, 'from B', 'Extracted archives', 'alb', 'trip.zip'))
        with zipfile.ZipFile(f'{B}/evil.zip', 'w') as z:
            z.writestr('../../escape.txt', b'x')
        try:
            Engine.extract_safe(f'{B}/evil.zip', f'{H}/x')
            raise AssertionError('zip-slip not blocked')
        except SystemExit:
            pass
        # sort by kind (sub-folders kept, project moved whole, hidden left) + duplicates found by content, any name
        S = f'{d}/S'
        for rel, data in (('trip/a.jpg', b'A'), ('trip/b.JPG', b'B'), ('notes/x.pdf', b'X'), ('code/main.py', b'm'),
                          ('code/pyproject.toml', b't'), ('trip/copy of a.jpg', b'A'), ('misc/thing.xyz', b'?'), ('.hidden', b'h')):
            os.makedirs(os.path.dirname(f'{S}/{rel}'), exist_ok=True)
            open(f'{S}/{rel}', 'wb').write(data)
        for rel, data in (('film/Film.mkv', b'V' * 50), ('film/English.srt', b's'), ('game/Game.exe', b'MZ'), ('game/data/a.pak', b'p'),
                          ('game/unins000.exe', b'u'), ('dl/desktop.ini', b'x'), ('dl/~$draft.docx', b'l'), ('dl/empty.txt', b''), ('album/01.mp3', b'M' * 40),
                          ('album/02.mp3', b'N' * 40), ('album/cover.jpg', b'c'), ('site/' + 'p1.html', b'1'), ('site/p2.html', b'2'),
                          ('site/p3.html', b'3'), ('site/p4.html', b'4'), ('site/p5.html', b'5'), ('site/img/a.gif', b'g')):
            os.makedirs(os.path.dirname(f'{S}/{rel}'), exist_ok=True)
            open(f'{S}/{rel}', 'wb').write(data)
        os.makedirs(f'{S}/$RECYCLE.BIN/S-1')
        open(f'{S}/$RECYCLE.BIN/S-1/$R1.jpg', 'wb').write(b'r')
        e4 = Engine({'quarantine': f'{S}/_q', 'labels': {S: 'S'}, 'scan': [S], 'rules': [
            {'keep': f'{S}/misc/thing.xyz'}, {'sort': S, 'to': {'photo': f'{S}/Pictures', 'document': f'{S}/Docs', 'project': f'{S}/Projects/',
                                                              'video': f'{S}/Videos', 'music': f'{S}/Music', 'app': f'{S}/Games/'}}]}, apply=True)
        e4.run(f'{d}/test4')
        assert os.path.isfile(f'{S}/Pictures/trip/a.jpg') and os.path.isfile(f'{S}/Pictures/trip/b.JPG')
        assert os.path.isfile(f'{S}/Docs/notes/x.pdf') and os.path.isfile(f'{S}/Projects/code/pyproject.toml')
        assert os.path.isfile(f'{S}/_q/from S/Duplicates/trip/copy of a.jpg') and e4.stats.get('DUPLICATE') == 1
        assert e4.stats.get('UNPLANNED') == 1 and e4.stats.get('KEPT') == 1 and os.path.isfile(f'{S}/misc/thing.xyz')
        assert os.path.isfile(f'{S}/.hidden') and os.path.isfile(f'{S}/$RECYCLE.BIN/S-1/$R1.jpg')       # hidden + OS folder untouched
        assert not os.path.exists(f'{S}/trip') and not os.path.exists(f'{S}/notes') and os.path.isdir(f'{S}/misc')  # emptied → removed
        assert os.path.isfile(f'{S}/Videos/film/English.srt') and os.path.isfile(f'{S}/Music/album/cover.jpg')    # sets stay together
        assert os.path.isfile(f'{S}/Games/game/data/a.pak') and os.path.isfile(f'{S}/Games/game/Game.exe')        # a game moves whole
        assert all(os.path.isfile(f'{S}/_q/from S/Junk/dl/{n}') for n in ('desktop.ini', '~$draft.docx', 'empty.txt'))
        assert os.path.isfile(f'{S}/Projects/site/img/a.gif')                                                     # a website moves whole
        assert verify(e4.log, [S]) == []
        open(f'{S}/Pictures/trip/x.part', 'w').write('half')
        os.rename(f'{S}/Docs/notes/x.pdf', f'{S}/Docs/x.pdf')
        assert len(verify(e4.log, [S])) == 2                                          # a missing file and a .part are caught
        # relink (symlink into a moved folder, a moved relative symlink, a launcher) and undo
        R = f'{d}/R'
        os.makedirs(f'{R}/Games/Lantern')
        open(f'{R}/Games/Lantern/run.sh', 'w').write('x')
        os.makedirs(f'{R}/Desk')
        open(f'{R}/Desk/Lantern.desktop', 'w').write(f'[Desktop Entry]\nPath={R}/Games/Lantern\nExec={R}/Games/Lantern/run.sh\n')
        os.symlink(f'{R}/Games/Lantern', f'{R}/Desk/lantern-abs')
        os.symlink('../Games/Lantern', f'{R}/Desk/lantern-rel')
        os.makedirs(f'{R}/Old')
        open(f'{R}/Old/a.txt', 'w').write('a')
        os.symlink('a.txt', f'{R}/Old/a-link')
        e3 = Engine({'quarantine': f'{R}/_q', 'labels': {R: 'R'}, 'rules': [
            {'move': f'{R}/Games/Lantern', 'to': f'{R}/Play/Lantern'}, {'move': f'{R}/Old/a-link', 'to': f'{R}/New/'}]}, apply=True)
        e3.run(f'{d}/test3')
        assert open(f'{R}/Desk/Lantern.desktop').read().count(f'{R}/Play/Lantern') == 2
        assert os.path.realpath(f'{R}/Desk/lantern-abs') == f'{R}/Play/Lantern'
        assert os.readlink(f'{R}/Desk/lantern-rel') == '../Play/Lantern' and os.path.isfile(f'{R}/Desk/lantern-rel/run.sh')
        assert open(f'{R}/New/a-link').read() == 'a' and e3.stats.get('RELINK') == 4
        assert os.path.dirname(e3.log_path) == R and 'move log' in e3.log_path
        stats, _ = undo(e3.log_path)
        assert os.path.isfile(f'{R}/Games/Lantern/run.sh') and os.readlink(f'{R}/Old/a-link') == 'a.txt'
        assert f'Path={R}/Games/Lantern' in open(f'{R}/Desk/Lantern.desktop').read() and os.readlink(f'{R}/Desk/lantern-rel') == '../Games/Lantern'
        assert stats.get('UNDO-MOVE') == 2 and not stats.get('UNDO-SKIPPED')
        # cross-device path (.part + md5) exercised directly
        open(f'{A}/q.bin', 'wb').write(os.urandom(2_000_000))
        h0 = e.md5(f'{A}/q.bin')
        e.copy_verified(f'{A}/q.bin', f'{B}/q.bin')
        assert e.md5(f'{B}/q.bin') == h0 and not os.path.exists(f'{A}/q.bin') and not os.path.exists(f'{B}/q.bin.part')
    print('selftest OK')


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        selftest()
        sys.exit()
    if len(sys.argv) == 3 and sys.argv[1] == '--verify':
        with open(sys.argv[2], newline='') as f:
            bad = verify(list(csv.reader(f))[1:])
        print('VERIFY: OK' if not bad else f'{len(bad)} PROBLEMS', *bad[:50], sep='\n')
        sys.exit(1 if bad else 0)
    if len(sys.argv) == 3 and sys.argv[1] == '--undo':
        stats, out = undo(sys.argv[2])
        print('UNDONE →', out)
        for k in sorted(stats):
            print(f'  {k:20} {stats[k]}')
        sys.exit()
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    plan_path = sys.argv[1]
    eng = Engine(json.load(open(plan_path)), apply='--apply' in sys.argv)
    prefix = os.path.splitext(plan_path)[0]
    if '--go' in sys.argv:
        chk = Engine(json.load(open(plan_path)))
        chk.build()
        miss = chk.unplanned()
        if miss:
            print(f'STOP, nothing changed: {len(miss)} UNPLANNED files. Add rules (first 40):', *miss[:40], sep='\n')
            sys.exit(2)
        eng = Engine(json.load(open(plan_path)), apply=True)
        eng.run(os.path.splitext(plan_path)[0])
        sys.exit(0 if report(eng) else 1)
    if '--check' in sys.argv:
        eng.build()
        miss = eng.unplanned()
        print(f'{len(miss)} UNPLANNED files'); print(*miss[:200], sep='\n')
        sys.exit(1 if miss else 0)
    stats = eng.run(prefix)
    print(('APPLIED' if eng.apply else 'DRY RUN, nothing changed'), '→', eng.log_path)
    for k in sorted(stats):
        print(f'  {k:20} {stats[k]}')
