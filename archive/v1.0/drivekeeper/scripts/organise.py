#!/usr/bin/env python3
"""Plan-driven, never-lose-anything file organiser.

  organise.py plan.json             dry run (default): changes nothing, writes <plan>-dryrun.csv
  organise.py plan.json --check     only lists UNPLANNED files (no rule covers them)
  organise.py plan.json --apply     does it, writes <plan>-log.csv (+ rename logs)
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
    {"extract": "/mnt/b/album.7z", "to": "/home/me/Pictures/Albums/"}
  ]
}
Works on Linux, macOS and Windows (Python 3.8+, stdlib only). On Windows write paths as "C:/Users/me/..."
(forward slashes) or escaped backslashes in JSON. .zip/.tar archives are extracted with Python; .7z/.rar need 7z.

Guarantees: never overwrites (a different file at the destination -> " (label)" suffix); never deletes except the
source of a verified cross-drive copy; cross-drive copies go to .part, are md5-verified, then renamed; duplicates
are confirmed by md5 before quarantining; nothing inside the quarantine counts as a "kept" copy; explicit renames
(rules with "log") only compare against their own destination; safe to re-run after an interruption.
"""
import csv, glob, hashlib, json, os, re, shutil, subprocess, sys, tarfile, tempfile, time, zipfile

SEP = os.sep
norm = os.path.normpath

RESERVE = 2 * 10**9                       # keep this much free on a destination filesystem
DATE = re.compile(r'(?<!\d)(19[89]\d|20[0-4]\d)[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])(?!\d{3})')
VIDEO = ('.mp4', '.mov', '.3gp', '.mkv', '.avi', '.webm', '.m4v')


class Engine:
    def __init__(self, plan, apply=False):
        self.plan, self.apply = plan, apply
        self.q = norm(plan['quarantine'])
        self.labels = {norm(k): v for k, v in plan.get('labels', {}).items()}
        self.progress_t, self.done_bytes = time.time(), 0
        self.ops, self.claimed, self.log = [], set(), []
        self.index, self.planned, self.moved, self.renames, self.stats = {}, {}, set(), {}, {}

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

    def build(self):
        for r in self.plan['rules']:
            if 'route' in r:
                if os.path.isdir(r['from']):
                    self.route_phone(norm(r['from']), norm(r['to']))
                continue
            key = next(k for k in ('move', 'each', 'quarantine', 'dedupe', 'extract') if k in r)
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

    def unplanned(self):
        def covered(p):
            while True:
                if p in self.claimed:
                    return True
                parent = os.path.dirname(p)
                if parent == p or not parent:
                    return False
                p = parent
        out = []
        for root in map(norm, self.plan.get('scan', [])):
            for dp, dn, fn in os.walk(root):
                if dp in self.claimed:
                    dn[:] = []
                    continue
                out += [os.path.join(dp, f) for f in fn if not covered(os.path.join(dp, f))]
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
        key = (os.path.basename(src), size)
        if allow_dup and not os.path.islink(src):
            other = dst if self.exists(dst) else (None if log else self.index.get(key))
            if other and other != src:
                real = other if self.apply else self.planned.get(other, other)
                if os.path.isfile(real) and os.path.getsize(real) == size and (not self.apply or self.md5(src) == self.md5(real)):
                    self.note('DUPLICATE', src, other, size, 'md5-equal' if self.apply else 'name+size equal (md5 checked on --apply)')
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
        if not dst.startswith(self.q + SEP):                       # never treat a quarantined copy as "kept"
            self.index.setdefault(key, dst)
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
        if self.apply:
            self.sweep_empty()
        with open(out_prefix + ('-log.csv' if self.apply else '-dryrun.csv'), 'w', newline='') as f:
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
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    plan_path = sys.argv[1]
    eng = Engine(json.load(open(plan_path)), apply='--apply' in sys.argv)
    prefix = os.path.splitext(plan_path)[0]
    if '--check' in sys.argv:
        eng.build()
        miss = eng.unplanned()
        print(f'{len(miss)} UNPLANNED files'); print(*miss[:200], sep='\n')
        sys.exit(1 if miss else 0)
    stats = eng.run(prefix)
    print(('APPLIED' if eng.apply else 'DRY RUN, nothing changed'), '→', prefix + ('-log.csv' if eng.apply else '-dryrun.csv'))
    for k in sorted(stats):
        print(f'  {k:20} {stats[k]}')
