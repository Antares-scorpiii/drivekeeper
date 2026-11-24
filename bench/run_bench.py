#!/usr/bin/env python3
"""Run every organiser skill on the same seeded datasets, sandboxed, and score the results.

  run_bench.py [--datasets home archive] [--only drivekeeper,composio] [--model claude-sonnet-5-5] [--effort low] [--parallel 3]
  Two dataset profiles (make_dataset.py --profile), each a different kind of mess. Run ids are <skill>.<profile>;
  non-default model/effort runs are tagged, e.g. drivekeeper@opus-5-5-low.home, so they never overwrite the Sonnet runs.
  run_bench.py --vendor        clone the competitor skills at the pinned commits into bench/vendor/
  --external driveA=/mnt/other/bench   keep that drive of every dataset on another disk (real cross-drive copies)

Linux only (uses bubblewrap): each run sees the whole system read-only, an empty $HOME, a writable copy of the
dataset and a throw-away Claude config holding just that one skill. The ground-truth manifest is outside the
sandbox. Results: bench/results/<id>.claude.json + <id>.score.json.
"""
import argparse, concurrent.futures as cf, json, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK, RES = os.path.join(HERE, 'work'), os.path.join(HERE, 'results')
HOME = os.path.expanduser('~')
DEFAULT_MODEL = 'claude-sonnet-5-5'
PROMPT = ("My computer's drives are a mess. Everything is under ./drives (three drives: driveA, driveB and home). "
          "Please organise all of it: photos and videos, documents, personal papers, books, music, movies and TV, "
          "software, games and code projects, and deal with duplicates, old versions and junk. I'm away and can't "
          "answer questions, so make reasonable decisions yourself and finish the job completely; you have my "
          "approval to apply the changes. Only work inside ./drives.")


def vendor():
    for c in json.load(open(os.path.join(HERE, 'competitors.json'))):
        if not c.get('commit'):
            continue
        repo = c['source'].split('github.com/')[1]
        dest = os.path.join(HERE, 'vendor', repo.replace('/', '_'))
        if not os.path.isdir(dest):
            subprocess.run(['git', 'clone', '-q', f'https://github.com/{repo}', dest], check=True)
        subprocess.run(['git', '-C', dest, 'checkout', '-q', c['commit']], check=True)
        print('ok', repo, c['commit'])


def dataset(profile):
    d = os.path.join(WORK, 'datasets', profile)
    if not os.path.exists(os.path.join(d, 'manifest.json')):
        subprocess.run([sys.executable, '-I', os.path.join(HERE, 'make_dataset.py'), '--profile', profile, '--seed', '1', '--out', d], check=True)
    return d


def one(c, profile, model, timeout, effort=None, external=()):
    tag = '' if model == DEFAULT_MODEL and not effort else '@' + model.replace('claude-', '') + (f'-{effort}' if effort else '')
    rid = f"{c['id']}{tag}.{profile}"
    run = os.path.join(WORK, 'runs', rid)
    ws, cfg = os.path.join(run, 'ws'), os.path.join(run, 'cfg')
    if os.path.exists(run):
        shutil.rmtree(run)
    os.makedirs(cfg)
    src = dataset(profile)
    shutil.copytree(os.path.join(src, 'drives'), os.path.join(ws, 'drives'), symlinks=True,
                    ignore=lambda d, names: [n for n in names if d == os.path.join(src, 'drives') and n in dict(external)])
    binds = []
    for name, base in external:                        # this drive lives on another disk, mounted into the sandbox
        far = os.path.join(base, rid, name)
        if os.path.exists(os.path.dirname(far)):
            shutil.rmtree(os.path.dirname(far))
        shutil.copytree(os.path.join(src, 'drives', name), far, symlinks=True)
        os.makedirs(os.path.join(ws, 'drives', name))
        binds += ['--bind', far, os.path.join(ws, 'drives', name)]
    shutil.copy2(os.path.join(src, 'manifest.json'), os.path.join(run, 'manifest.json'))   # outside ws: invisible in the sandbox
    shutil.copy2(os.path.join(HOME, '.claude', '.credentials.json'), os.path.join(cfg, '.credentials.json'))
    prompt = PROMPT
    if c['skill_dir']:
        name = os.path.basename(c['skill_dir'].rstrip('/'))
        shutil.copytree(os.path.join(HERE, c['skill_dir']), os.path.join(cfg, 'skills', name))
        prompt = f"Use the {c['display']} skill for this task. " + PROMPT
    cmd = ['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp', '--tmpfs', HOME,
           '--ro-bind', f'{HOME}/.local/bin', f'{HOME}/.local/bin',
           '--ro-bind', f'{HOME}/.local/share/claude', f'{HOME}/.local/share/claude',
           '--bind', ws, ws, *binds, '--bind', cfg, cfg, '--chdir', ws,
           '--setenv', 'CLAUDE_CONFIG_DIR', cfg, '--setenv', 'HOME', HOME, '--die-with-parent', '--',
           f'{HOME}/.local/bin/claude', '-p', prompt, '--model', model, '--output-format', 'json',
           '--dangerously-skip-permissions', '--max-turns', '300'] + (['--effort', effort] if effort else [])
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out, status = p.stdout, f'exit {p.returncode}'
        err = p.stderr[-2000:]
    except subprocess.TimeoutExpired as e:
        out, status, err = (e.stdout or b'').decode() if isinstance(e.stdout, bytes) else (e.stdout or ''), 'timeout', ''
    wall = time.time() - t0
    for name, base in external:                        # let the scorer see the far drive where the sandbox saw it
        mp = os.path.join(ws, 'drives', name)
        os.rmdir(mp)
        os.symlink(os.path.join(base, rid, name), mp)
    os.makedirs(RES, exist_ok=True)
    raw = os.path.join(RES, f'{rid}.claude.json')
    try:
        j = json.loads(out)
    except ValueError:
        j = {'is_error': True, 'subtype': status, 'raw_tail': out[-3000:], 'stderr_tail': err}
    j['wall_s'], j['status'], j['skill'], j['dataset'], j['model'], j['effort'] = round(wall, 1), status, c['id'], profile, model, effort
    json.dump(j, open(raw, 'w'), indent=1)
    sc = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'score.py'), run, '--claude-json', raw,
                         '--out', os.path.join(RES, f'{rid}.score.json')], capture_output=True, text=True)
    print(f'[{rid}] {status} in {wall/60:.1f} min', flush=True)
    return rid, sc.returncode


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--datasets', nargs='+', default=['home', 'archive'], choices=['home', 'archive'])
    ap.add_argument('--only')
    ap.add_argument('--model', default=DEFAULT_MODEL)
    ap.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh', 'max'])
    ap.add_argument('--parallel', type=int, default=3)
    ap.add_argument('--timeout', type=int, default=45 * 60)
    ap.add_argument('--vendor', action='store_true')
    ap.add_argument('--external', action='append', default=[], metavar='DRIVE=DIR')
    a = ap.parse_args()
    if a.vendor:
        vendor()
        sys.exit()
    comps = json.load(open(os.path.join(HERE, 'competitors.json')))
    if a.only:
        comps = [c for c in comps if c['id'] in a.only.split(',')]
    for d in a.datasets:
        dataset(d)
    jobs = [(c, d) for d in a.datasets for c in comps]
    with cf.ThreadPoolExecutor(a.parallel) as ex:
        for f in cf.as_completed([ex.submit(one, c, s, a.model, a.timeout, a.effort, tuple(e.split('=', 1) for e in a.external)) for c, s in jobs]):
            f.result()
