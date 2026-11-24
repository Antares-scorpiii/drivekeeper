#!/usr/bin/env python3
"""Read-only survey helpers. Never moves or changes anything.

  inventory.py overview ROOT... [--out DIR]  START HERE: the whole picture in one walk, ~80 lines; full lists to DIR
  inventory.py survey ROOT [--depth 2]       per-folder size / file count / top extensions (no file dump)
  inventory.py dups ROOT [ROOT...]           duplicates: same size, CONFIRMED by md5, any name (writes dups.csv)
  inventory.py hidden ROOT [ROOT...]         hidden files/folders, split into "content" vs "app/project config"
  inventory.py dates ROOT                    date range found in file names per folder (phone dumps, exports)
  inventory.py links ROOT [ROOT...]          symlinks and launchers (.desktop/.lnk/.url) with their targets
  inventory.py drives                        mounted drives / volumes with size, used, free (Linux, macOS, Windows)

Works on Linux, macOS and Windows (Python 3.8+, stdlib only). Hidden = dot-name, or the Windows hidden attribute.
"""
import collections, csv, hashlib, os, re, shutil, stat, string, sys

WINDOWS_SYSTEM = {'$RECYCLE.BIN', 'System Volume Information', 'desktop.ini', 'Thumbs.db', 'pagefile.sys', 'hiberfil.sys', 'swapfile.sys'}
FUNCTIONAL = {'$RECYCLE.BIN', 'System Volume Information', '.git', '.venv', 'venv', 'node_modules', '.obsidian', '.idea', '.vscode', '.github', '.env',
              '.gitignore', '.claude', '.Trash-1000', '.Trash', 'lost+found', '.cache', '.config', '.local'}
DATE = re.compile(r'(?<!\d)(19[89]\d|20[0-4]\d)[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])')


def walk(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ('.git', 'node_modules', '.venv', 'site-packages', '__pycache__')]
        yield dp, dn, fn


def survey(root, depth):
    size, count, ext = collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter)
    for dp, _, fn in walk(root):
        rel = os.path.relpath(dp, root).split(os.sep)
        key = '/'.join(rel[:depth]) if rel != ['.'] else '.'
        for f in fn:
            p = os.path.join(dp, f)
            try:
                s = os.lstat(p).st_size
            except OSError:
                continue
            size[key] += s
            count[key] += 1
            ext[key][os.path.splitext(f)[1].lower() or '(none)'] += 1
    for k in sorted(size, key=lambda k: -size[k]):
        top = ' '.join(f'{e}:{n}' for e, n in ext[k].most_common(5))
        print(f'{size[k]/1e9:8.2f} GB {count[k]:7}  {k}   {top}')


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def dups(roots):
    by_size = collections.defaultdict(list)                       # only files sharing a size can be identical
    for r in roots:
        for dp, _, fn in walk(r):
            for f in fn:
                p = os.path.join(dp, f)
                if os.path.isfile(p) and not os.path.islink(p) and os.path.getsize(p) > 0:
                    by_size[os.path.getsize(p)].append(p)
    confirmed, names = [], collections.defaultdict(set)
    for s, ps in by_size.items():
        if len(ps) < 2:
            continue
        by_hash = collections.defaultdict(list)
        for p in ps:
            h = md5(p)
            by_hash[h].append(p)
            names[(os.path.basename(p), s)].add(h)
        confirmed += [(h, s, p) for h, g in by_hash.items() if len(g) > 1 for p in g]
    traps = sum(len(hs) > 1 for hs in names.values())             # same name+size, different content: NOT duplicates
    with open('dups.csv', 'w', newline='') as f:
        csv.writer(f).writerows([('md5', 'bytes', 'path')] + sorted(confirmed))
    n = len({h for h, _, _ in confirmed})
    extra = sum(s for h, s, p in confirmed) - sum({h: s for h, s, _ in confirmed}.values())
    print(f'{n} duplicate groups, {len(confirmed)} files, {extra/1e9:.2f} GB in extra copies → dups.csv')
    print(f'{traps} name+size matches that are NOT identical (different content) — keep both')


def is_hidden(path, name):
    if name.startswith('.'):
        return True
    attrs = getattr(os.lstat(path), 'st_file_attributes', 0)      # Windows only
    return bool(attrs & getattr(stat, 'FILE_ATTRIBUTE_HIDDEN', 2))


def hidden(roots, limit=300):
    content, functional = [], collections.Counter()
    for r in roots:
        for dp, dn, fn in os.walk(r):
            for x in dn + fn:
                if is_hidden(os.path.join(dp, x), x):
                    p = os.path.join(dp, x)
                    if x in FUNCTIONAL or os.path.basename(dp) in FUNCTIONAL:
                        functional[x] += 1
                    else:
                        content.append(p)
            dn[:] = [d for d in dn if d not in FUNCTIONAL]
    print('functional (leave hidden):', dict(functional.most_common(15)))
    print(f'{len(content)} hidden items that may hold content (thumbnails, .trashed-*, .pending, markers):')
    for p in content[:limit]:
        s = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(p) for f in fs) if os.path.isdir(p) else os.path.getsize(p)
        print(f'  {s:>10}  {p}')


def drives():
    if os.name == 'nt':
        mounts = [f'{l}:\\' for l in string.ascii_uppercase if os.path.exists(f'{l}:\\')]
    else:
        mounts = []
        with open('/proc/mounts' if os.path.exists('/proc/mounts') else os.devnull) as f:
            for line in f:
                dev, mnt, fs = line.split()[:3]
                if dev.startswith('/dev/') or fs in ('fuseblk', 'ntfs', 'ntfs3', 'exfat', 'vfat'):
                    mounts.append((mnt.replace('\\040', ' '), fs))
        if not mounts:                                             # macOS
            mounts = [('/', '?')] + [(os.path.join('/Volumes', v), '?') for v in os.listdir('/Volumes')] if os.path.isdir('/Volumes') else [('/', '?')]
    print(f'{"mount":40} {"fs":8} {"size GB":>9} {"used GB":>9} {"free GB":>9}')
    seen = set()
    for m in mounts:
        mnt, fs = (m, '') if isinstance(m, str) else m
        try:
            u = shutil.disk_usage(mnt)
        except OSError:
            continue
        if (u.total, u.used) in seen:
            continue
        seen.add((u.total, u.used))
        print(f'{mnt:40} {fs:8} {u.total/1e9:9.1f} {u.used/1e9:9.1f} {u.free/1e9:9.1f}')
    print('(decimal GB; Explorer/Dolphin/df -h show GiB, about 7% smaller numbers)')


def dates(root, limit=None):
    rng = collections.defaultdict(list)
    for dp, _, fn in walk(root):
        for f in fn:
            m = DATE.search(f)
            if m:
                rng[os.path.relpath(dp, root)].append(''.join(m.groups()))
    for k, v in sorted(rng.items())[:limit]:
        print(f'{min(v)} → {max(v)}  {len(v):6} files  {k}')
    if limit and len(rng) > limit:
        print(f'… {len(rng) - limit} more folders (run: inventory.py dates {root})')


LAUNCHER = ('.desktop', '.lnk', '.url', '.webloc')


def links(roots, limit=100):
    """Symlinks and launchers: after a move they may point at the old place."""
    out = []
    for r in roots:
        for dp, dn, fn in os.walk(r):
            for x in dn + fn:
                p = os.path.join(dp, x)
                if os.path.islink(p):
                    t = os.readlink(p)
                    ok = os.path.exists(os.path.join(dp, t))
                    out.append(f'  symlink{"" if ok else " (BROKEN)"}  {p} -> {t}')
                elif x.lower().endswith(LAUNCHER) and os.path.isfile(p):
                    try:
                        with open(p, errors='replace') as f:
                            txt = f.read(4000)
                    except OSError:
                        txt = ''
                    targets = re.findall(r'^(?:Exec|Path|URL)=(.*)$', txt, re.M)
                    out.append(f'  launcher  {p}  {"; ".join(targets)[:160] or "(binary .lnk: check by hand)"}')
    print(f'{len(out)} symlinks/launchers (the engine re-points those whose target it moves; check the rest after apply):')
    print(*out[:limit], sep='\n')


def overview(roots, out='.'):
    """The whole picture in one walk, capped at ~80 printed lines; full lists go to <out>/survey-details.txt and
    <out>/dups.csv. Answers what an organiser needs before writing a plan, so no exploratory listing is needed."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from organise import KINDS, kind, is_project
    import unicodedata, zipfile, tarfile
    MAGIC = [(b'%PDF', 'pdf', ('.pdf', '.ai')), (b'\xff\xd8\xff', 'jpeg', ('.jpg', '.jpeg')), (b'\x89PNG', 'png', ('.png',)),
             (b'GIF8', 'gif', ('.gif',))]
    OS_DIRS = ('.Trash', '$RECYCLE.BIN', 'System Volume Information', '.Spotlight-V100', '.fseventsd', '.Trashes', 'lost+found')
    files, sizes, size_of = [], collections.Counter(), {}
    kinds, kind_bytes, other_ext = collections.Counter(), collections.Counter(), collections.Counter()
    projects, os_dirs, hidden, links, odd, wrong, collide, empty_dirs, empty_files = [], [], collections.defaultdict(list), [], [], [], [], [], 0
    dated = collections.defaultdict(list)
    for r in roots:
        for dp, dn, fn in os.walk(r):
            rel = os.path.relpath(dp, r)
            top = os.path.join(os.path.basename(r.rstrip(os.sep)), *rel.split(os.sep)[:2]) if rel != '.' else os.path.basename(r)
            names = dn + fn
            if dp != r and is_project(dp, names):
                projects.append(dp)
            for d in list(dn):
                full = os.path.join(dp, d)
                if d.startswith(OS_DIRS):
                    os_dirs.append(full); dn.remove(d)
                elif d in FUNCTIONAL:
                    dn.remove(d)
                elif os.path.islink(full):
                    links.append((full, os.readlink(full)))
            if not dn and not fn:
                empty_dirs.append(dp)
            folded = collections.defaultdict(set)
            for n in names:
                folded[unicodedata.normalize('NFC', n).casefold()].add(n)
            collide += [sorted(v) for v in folded.values() if len(v) > 1]
            for f in fn:
                p = os.path.join(dp, f)
                if os.path.islink(p):
                    links.append((p, os.readlink(p))); continue
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if is_hidden(p, f) or any(x.startswith('.') for x in rel.split(os.sep) if x != '.'):
                    tag = ('.trashed' if f.startswith('.trashed') else '.thumbnails' if '.thumbnails' in p else
                           'empty marker' if st.st_size == 0 else 'other hidden')
                    hidden[tag].append((p, st.st_size)); continue
                files.append(p); size_of[p] = st.st_size; sizes[top] += st.st_size
                k = kind(f); kinds[k] += 1; kind_bytes[k] += st.st_size
                if k == 'other':
                    other_ext[os.path.splitext(f)[1].lower() or '(none)'] += 1
                if st.st_size == 0:
                    empty_files += 1
                if re.search(r'[\x00-\x1f]', f) or f[:1] in '- ' or f[-1:] in ' .' or len(p) > 240:
                    odd.append(p)
                m = DATE.search(f)
                if m:
                    dated[dp].append(''.join(m.groups()))
                if st.st_size:
                    with open(p, 'rb') as fh:
                        head = fh.read(8)
                    ext = os.path.splitext(f)[1].lower()
                    real = next((name for sig, name, _ in MAGIC if head.startswith(sig)), None)
                    claims = next((name for _, name, exts in MAGIC if ext in exts), None)
                    if real and real != claims:
                        wrong.append(f'{p}  (content is {real}{", extension says " + claims if claims else ""})')
                    elif claims and not real:
                        wrong.append(f'{p}  (not a real {claims})')
    # duplicates: md5 only within same-size groups
    by_size = collections.defaultdict(list)
    for p in files:
        if size_of[p]:
            by_size[size_of[p]].append(p)
    groups, md5_of = collections.defaultdict(list), {}
    for s_, ps in by_size.items():
        if len(ps) > 1:
            for p in ps:
                md5_of[p] = md5(p)
                groups[(s_, md5_of[p])].append(p)
    groups = {k: v for k, v in groups.items() if len(v) > 1}
    by_name = collections.defaultdict(list)
    for p in md5_of:
        by_name[(os.path.basename(p), size_of[p])].append(p)
    look = [' | '.join(ps) for ps in by_name.values() if len({md5_of[p] for p in ps}) > 1]
    pairs = collections.Counter(tuple(sorted({os.path.dirname(p) for p in v})[:2]) for v in groups.values())
    with open(os.path.join(out, 'dups.csv'), 'w', newline='') as fh:
        csv.writer(fh).writerows([('md5', 'bytes', 'path')] + [(h, s_, p) for (s_, h), v in sorted(groups.items()) for p in v])
    # archives: integrity + whether their members already exist on disk (name + size)
    on_disk = {(os.path.basename(p), size_of[p]) for p in files}
    arcs = []
    for p in files:
        if kind(p) != 'archive':
            continue
        try:
            if zipfile.is_zipfile(p):
                with zipfile.ZipFile(p) as z:
                    mem = [i for i in z.infolist() if not i.is_dir()]
                    bad = z.testzip() if size_of[p] < 5e8 else None
                    found = sum((os.path.basename(i.filename), i.file_size) in on_disk for i in mem)
                    arcs.append(f'{p}  zip, {len(mem)} files, {"CORRUPT member " + bad if bad else "ok"}, {found}/{len(mem)} already on disk')
            elif tarfile.is_tarfile(p):
                with tarfile.open(p) as t:
                    mem = [m for m in t.getmembers() if m.isfile()]
                    found = sum((os.path.basename(m.name), m.size) in on_disk for m in mem)
                    arcs.append(f'{p}  tar, {len(mem)} files, ok, {found}/{len(mem)} already on disk')
            elif p.lower().endswith(('.zip', '.tar', '.gz', '.tgz')):
                arcs.append(f'{p}  CORRUPT or truncated (not a readable {os.path.splitext(p)[1]}): keep it, never extract')
            else:
                arcs.append(f'{p}  7z/rar/multi-part: {"list with 7z l" if shutil.which("7z") else "needs 7-Zip to read; keep together"}')
        except Exception as e:
            arcs.append(f'{p}  CORRUPT ({type(e).__name__})')
    # print (capped) + details file
    full, shown = [], []
    base = os.path.commonpath([os.path.abspath(r) for r in roots]) if len(roots) > 1 else os.path.dirname(os.path.abspath(roots[0]))
    short = lambda x: str(x).replace(base + os.sep, '')[:200]
    def sect(title, rows, cap):
        full.append(f'== {title} ({len(rows)})'); full.extend(map(str, rows))
        shown.append(f'== {title} ({len(rows)})'); shown.extend(f'  {short(x)}' for x in rows[:cap])
        if len(rows) > cap:
            shown.append(f'  … {len(rows) - cap} more in survey-details.txt')
    vol = []
    for r in roots:
        u = shutil.disk_usage(r)
        vol.append(f'{r}  device {os.stat(r).st_dev}  free {u.free / 1e9:.1f} GB')
    sect('volumes (same device = instant renames; different = copy + verify)', vol, 10)
    sect('biggest folders (depth 2)', [f'{v / 1e6:9.1f} MB  {k}' for k, v in sizes.most_common()], 12)
    sect('kinds', [f'{k:9} {kinds[k]:6} files {kind_bytes[k] / 1e6:9.1f} MB' for k in sorted(kinds, key=lambda k: -kinds[k])]
         + [f'other extensions: ' + ' '.join(f'{e}:{n}' for e, n in other_ext.most_common(15))], 15)
    sect('projects: move each as one unit, never sort inside', projects, 10)
    sect('folders with dates in file names (first → last, count)',
         [f'{min(v)} → {max(v)} {len(v):5}  {k}' for k, v in sorted(dated.items(), key=lambda kv: -len(kv[1]))], 10)
    sect(f'duplicates by content (md5): {sum(len(v) - 1 for v in groups.values())} extra copies; folder pairs → dups.csv',
         [f'{n:5}  {" <-> ".join(k)}' for k, n in pairs.most_common()], 10)
    sect('same name + size but DIFFERENT content: keep both', look, 3)
    sect('names that collide on Windows/macOS (case or Unicode form)', collide, 5)
    sect('archives', arcs, 12)
    sect('wrong or missing extension', wrong, 10)
    sect('awkward names (control chars, leading -/space, trailing space/dot, very long)', odd, 8)
    sect('OS-managed folders: leave alone', os_dirs, 8)
    sect('hidden / app-managed (the user decides)', [f'{k}: {len(v)} files, {sum(x for _, x in v) / 1e6:.1f} MB, e.g. {v[0][0]}' for k, v in hidden.items()], 8)
    sect('symlinks and launchers', [f'{p} -> {t}' for p, t in links] + [p for p in files if p.lower().endswith(LAUNCHER)], 8)
    sect(f'empty: {empty_files} empty files; empty folders', empty_dirs, 5)
    with open(os.path.join(out, 'survey-details.txt'), 'w') as fh:
        fh.write('\n'.join(full) + '\n')
    print(f'Paths below are relative to {base}')
    print(f'{len(files)} files, {sum(size_of.values()) / 1e9:.2f} GB under {len(roots)} roots (contents of .git, node_modules, .venv '
          f'not counted: they move with their project). Full lists: '
          f'{os.path.join(out, "survey-details.txt")}, {os.path.join(out, "dups.csv")}')
    print('\n'.join(shown))


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == 'drives':
        drives()
        sys.exit()
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    argv = sys.argv[2:]
    out = '.'
    if '--out' in argv:
        i = argv.index('--out')
        out = argv[i + 1]
        del argv[i:i + 2]
        os.makedirs(out, exist_ok=True)
    depth = 2
    if '--depth' in argv:
        i = argv.index('--depth')
        depth = int(argv[i + 1])
        del argv[i:i + 2]
    cmd, args = sys.argv[1], argv
    {'survey': lambda: survey(args[0], depth), 'dups': lambda: dups(args), 'hidden': lambda: hidden(args),
     'dates': lambda: dates(args[0]), 'links': lambda: links(args), 'overview': lambda: overview(args, out)}[cmd]()
