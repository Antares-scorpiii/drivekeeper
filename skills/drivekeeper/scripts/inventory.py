#!/usr/bin/env python3
"""Read-only survey helpers. Never moves or changes anything.

  inventory.py overview ROOT [ROOT...]       everything below in one call, capped output (start here)
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


def overview(roots):
    for r in roots:
        print(f'== survey {r}')
        survey(r, 2)
    for r in roots:
        print(f'== dates {r}')
        dates(r, limit=40)
    print('== hidden')
    hidden(roots, limit=40)
    print('== links')
    links(roots, limit=40)
    print('== dups')
    dups(roots)


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == 'drives':
        drives()
        sys.exit()
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    argv = sys.argv[2:]
    depth = 2
    if '--depth' in argv:
        i = argv.index('--depth')
        depth = int(argv[i + 1])
        del argv[i:i + 2]
    cmd, args = sys.argv[1], argv
    {'survey': lambda: survey(args[0], depth), 'dups': lambda: dups(args), 'hidden': lambda: hidden(args),
     'dates': lambda: dates(args[0]), 'links': lambda: links(args), 'overview': lambda: overview(args)}[cmd]()
