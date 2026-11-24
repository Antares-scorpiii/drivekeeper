#!/usr/bin/env python3
"""Aggregate bench/results/*.score.json and draw README charts (light + dark PNG) + summary tables.

  charts.py            → bench/charts/{light,dark}/*.png, bench/results/summary.csv, bench/results/summary.md

Form choices (dataviz method): one quantity per chart, no dual axes; skills are named on the axis so colour is
not needed for identity — "highlight" form: this repo's skill in Claude clay, the others warm grey (grey is
deliberately achromatic; every bar carries its value as text, which is the contrast relief); the metric heatmap
uses one sequential clay ramp with values printed in every cell; data loss uses the reserved critical red with a
text label. Mean over the three datasets; whiskers show min–max across datasets.
Look: Claude's warm surfaces + a slight hand-drawn wobble (matplotlib path.sketch) + Patrick Hand (OFL, fonts/).
Runs tagged with a model/effort (e.g. drivekeeper@opus-5-5-low) are plotted as their own rows.
"""
import collections, csv, glob, json, os, re
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap

HERE = os.path.dirname(os.path.abspath(__file__))
RES, OUT = os.path.join(HERE, 'results'), os.path.join(HERE, 'charts')
OURS = 'drivekeeper'
THEMES = {   # validated with the dataviz validator: clay vs grey passes CVD (ΔE 15.6 light / 9.5 dark) and lightness band
    'light': dict(surface='#faf9f5', ink='#141413', ink2='#5e5d59', muted='#87867f', grid='#e8e6dc', accent='#c6613f',
                  other='#b0aea5', critical='#bf4d43', ramp=['#f9ebe4', '#f2cdbb', '#e9a98c', '#d97757', '#b4532f', '#7d3519']),
    'dark': dict(surface='#262624', ink='#faf9f5', ink2='#c2c0b6', muted='#87867f', grid='#3a3a37', accent='#d4704f',
                 other='#6b6a65', critical='#e5695c', ramp=['#3a2b25', '#5e3626', '#8c4a30', '#c0623e', '#e08a6d', '#f2c4b0']),
}
SKETCH = (0.9, 110, 2)   # path.sketch (scale, length, randomness): a light hand-drawn wobble; None = clean lines
FONT = os.path.join(HERE, 'fonts', 'PatrickHand-Regular.ttf')
if os.path.exists(FONT):
    font_manager.fontManager.addfont(FONT)
    plt.rcParams['font.family'] = [font_manager.FontProperties(fname=FONT).get_name(), 'DejaVu Sans']   # fallback for ★
plt.rcParams['path.sketch'] = SKETCH
RATE_METRICS = [('placement_accuracy', 'Placement'), ('coverage', 'Coverage'), ('dedup_rate', 'Dedup'),
                ('junk_cleared', 'Junk cleared'), ('together_integrity', 'Sets kept together'), ('trap_safety', 'Trap safety'),
                ('project_integrity', 'Projects intact'), ('game_integrity', 'Games intact'), ('app_integrity', 'Apps intact'),
                ('injection_resisted', 'Ignored injection')]


def load():
    labels = {c['id']: c for c in json.load(open(os.path.join(HERE, 'competitors.json')))}
    runs = collections.defaultdict(list)
    for f in sorted(glob.glob(os.path.join(RES, '*.score.json'))):
        rid = os.path.basename(f)[:-len('.score.json')]
        skill = rid.rsplit('.', 1)[0]                                   # <skill>[@model-effort].<dataset>
        m = json.load(open(f))['metrics']
        if m.get('is_error'):                           # cut off (usage limit, crash): not a result
            print('skipping incomplete run', rid)
            continue
        if 'input_tokens' in m:                         # Claude Code reports most input as cache reads/writes
            m['input_tokens_total'] = m['input_tokens'] + m.get('cache_read_tokens', 0) + m.get('cache_write_tokens', 0)
        runs[skill].append(m)
    return labels, runs


def agg(runs, key):
    out = {}
    for s, ms in runs.items():
        v = [m.get(key) for m in ms if isinstance(m.get(key), (int, float))]
        if v:
            out[s] = (sum(v) / len(v), min(v), max(v))
    return out


def flat(fig, ax):
    for p in (fig.patch, ax.patch):   # keep backgrounds straight so the wobble never exposes the page edge
        p.set_sketch_params(None)


def style(ax, t):
    ax.set_facecolor(t['surface'])
    flat(ax.figure, ax)
    for side in ('top', 'right', 'left'):
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(t['muted'])
    ax.tick_params(colors=t['ink2'], labelsize=12, length=0)
    ax.grid(axis='x', color=t['grid'], linewidth=0.8)
    ax.set_axisbelow(True)


def name(labels, s):
    base, _, cfg = s.partition('@')                                    # e.g. drivekeeper@opus-5-5-low
    c = labels.get(base, {})
    tag = f" ({c['installs']} installs)" if c.get('installs') else ''
    model = ' · ' + re.sub(r'(\d)-(\d)', r'\1.\2', cfg).replace('-', ' ').title() if cfg else ''   # Opus 5.5 Low
    return (f'{base} ★' if base == OURS else base) + model + tag


def hbar(fig_name, title, subtitle, data, labels, t, fmt, xmax=None, lower_better=False, color_fn=None):
    items = sorted(data.items(), key=lambda kv: kv[1][0], reverse=not lower_better)
    items.reverse()                                                     # best at the top
    fig, ax = plt.subplots(figsize=(8.2, 0.55 * len(items) + 1.6), facecolor=t['surface'])
    style(ax, t)
    for i, (s, (mean, lo, hi)) in enumerate(items):
        col = color_fn(s, mean) if color_fn else (t['accent'] if s.startswith(OURS) else t['other'])
        ax.barh(i, mean, height=0.56, color=col, edgecolor=t['surface'], linewidth=2)
        if hi > lo:
            ax.plot([lo, hi], [i, i], color=t['ink2'], linewidth=1.4, solid_capstyle='round')
        ax.text(max(hi, mean) + (xmax or max(v[2] for v in data.values()) or 1) * 0.015, i, fmt(mean),
                va='center', ha='left', color=t['ink'], fontsize=10)
    ax.set_yticks(range(len(items)), [name(labels, s) for s, _ in items])
    for lbl in ax.get_yticklabels():
        if lbl.get_text().startswith(OURS):
            lbl.set_color(t['ink']); lbl.set_fontweight('bold')
    ax.set_xlim(0, (xmax or max(v[2] for v in data.values()) * 1.18 or 1))
    fig.text(0.012, 0.985, title, ha='left', va='top', fontsize=17, fontweight='bold', color=t['ink'])
    fig.text(0.012, 0.985 - 0.32 / fig.get_figheight(), subtitle, ha='left', va='top', fontsize=12, color=t['ink2'])
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.55 / fig.get_figheight()))
    fig.savefig(os.path.join(OUT, fig_name), dpi=160, facecolor=t['surface'])
    plt.close(fig)


def heatmap(runs, labels, t, path):
    skills = sorted(runs, key=lambda s: -agg(runs, 'organisation_score').get(s, (0,))[0])
    vals = [[agg({s: runs[s]}, k).get(s, (float('nan'),))[0] for k, _ in RATE_METRICS] for s in skills]
    cmap = LinearSegmentedColormap.from_list('seq', t['ramp'])
    fig, ax = plt.subplots(figsize=(11, 0.55 * len(skills) + 2.2), facecolor=t['surface'])
    ax.set_facecolor(t['surface'])
    flat(fig, ax)
    ax.imshow(vals, cmap=cmap, vmin=0, vmax=1, aspect='auto')
    for i, row in enumerate(vals):
        for j, v in enumerate(row):
            dark_cell = v == v and v > 0.55
            ink = ('#ffffff' if dark_cell else '#0b0b0b') if t is THEMES['light'] else ('#0b0b0b' if v == v and v > 0.7 else '#f0efec')
            ax.text(j, i, '–' if v != v else f'{v*100:.0f}', ha='center', va='center', fontsize=10, color=ink)
    ax.set_xticks(range(len(RATE_METRICS)), [l for _, l in RATE_METRICS], rotation=25, ha='right', color=t['ink2'], fontsize=10)
    ax.set_yticks(range(len(skills)), [name(labels, s) for s in skills], color=t['ink2'], fontsize=10)
    for lbl in ax.get_yticklabels():
        if lbl.get_text().startswith(OURS):
            lbl.set_color(t['ink']); lbl.set_fontweight('bold')
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_xticks([x + 0.5 for x in range(len(RATE_METRICS) - 1)], minor=True)
    ax.set_yticks([y + 0.5 for y in range(len(skills) - 1)], minor=True)
    ax.grid(which='minor', color=t['surface'], linewidth=2)
    fig.text(0.012, 0.985, 'Every metric, per skill (0–100, higher is better)', ha='left', va='top', fontsize=17,
             fontweight='bold', color=t['ink'])
    fig.text(0.012, 0.985 - 0.32 / fig.get_figheight(), 'Mean over datasets; ' + ('darker' if t is THEMES['light'] else 'brighter') + ' = better',
             ha='left', va='top', fontsize=12, color=t['ink2'])
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.55 / fig.get_figheight()))
    fig.savefig(path, dpi=160, facecolor=t['surface'])
    plt.close(fig)


def tables(runs, labels):
    keys = ['safety_score', 'organisation_score', 'critical_lost', 'disposable_deleted', 'placement_accuracy', 'coverage',
            'dedup_rate', 'junk_cleared', 'together_integrity', 'trap_safety', 'project_integrity', 'game_integrity',
            'app_integrity', 'injection_resisted', 'info_hidden_made_visible', 'info_pc_files_left_in_place',
            'info_os_folders_untouched', 'empty_dirs_left', 'duration_s', 'turns', 'input_tokens_total', 'input_tokens', 'cache_read_tokens', 'cache_write_tokens',
            'output_tokens', 'total_tokens', 'cost_usd']
    rows = []
    for s in sorted(runs, key=lambda s: -agg(runs, 'safety_score').get(s, (0,))[0]):
        r = {'skill': s, 'runs': len(runs[s]), 'installs': labels.get(s.partition('@')[0], {}).get('installs') or ''}
        for k in keys:
            v = agg({s: runs[s]}, k).get(s)
            r[k] = round(v[0], 3) if v else ''
        rows.append(r)
    with open(os.path.join(RES, 'summary.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    pct = lambda v: '' if v == '' else f'{v*100:.0f}%'
    md = ['| Skill | Safety | Organisation | Critical files lost | Placement | Dedup | Junk cleared | Sets kept together | Projects intact | Ignored injection | Time (min) | Turns | Input tokens | Output tokens | Cost ($) |',
          '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        skill = f"**{r['skill']}**" if r['skill'].startswith(OURS) else r['skill']
        minutes = '' if r['duration_s'] == '' else round(r['duration_s'] / 60, 1)
        mtok = lambda v: '' if v == '' else f'{v / 1e6:.2f}M'
        ktok = lambda v: '' if v == '' else f'{v / 1e3:.0f}K'
        md.append(f"| {skill} | {r['safety_score']} | {r['organisation_score']} | {r['critical_lost']} | "
                  f"{pct(r['placement_accuracy'])} | {pct(r['dedup_rate'])} | {pct(r['junk_cleared'])} | "
                  f"{pct(r['together_integrity'])} | {pct(r['project_integrity'])} | {pct(r['injection_resisted'])} | {minutes} | {r['turns']} | {mtok(r['input_tokens_total'])} | {ktok(r['output_tokens'])} | {r['cost_usd']} |")
    open(os.path.join(RES, 'summary.md'), 'w').write('\n'.join(md) + '\n')


def main():
    labels, runs = load()
    if not runs:
        raise SystemExit('no results yet: run run_bench.py first')
    tables(runs, labels)
    for mode, t in THEMES.items():
        global OUT
        OUT = os.path.join(HERE, 'charts', mode)
        os.makedirs(OUT, exist_ok=True)
        pct = lambda v: f'{v:.0f}'
        hbar('safety.png', 'Safety score', 'No lost files, traps survive, trash/projects/games intact (0–100)',
             agg(runs, 'safety_score'), labels, t, pct, xmax=112)
        hbar('organisation.png', 'Organisation score', 'Right folder, duplicates collapsed, junk and hidden files handled (0–100)',
             agg(runs, 'organisation_score'), labels, t, pct, xmax=112)
        lost = agg(runs, 'critical_lost')
        hbar('data_loss.png', 'Files permanently lost', 'Unique photos, documents, projects… gone from every drive (lower is better)',
             lost, labels, t, lambda v: f'{v:.0f}', lower_better=True,
             color_fn=lambda s, v: t['critical'] if v > 0 else (t['accent'] if s.startswith(OURS) else t['other']))
        hbar('time.png', 'Wall-clock time', 'Minutes per run (lower is better)',
             {s: tuple(x / 60 for x in v) for s, v in agg(runs, 'duration_s').items()}, labels, t, lambda v: f'{v:.1f} min', lower_better=True)
        hbar('input_tokens.png', 'Input tokens', 'Fresh + cached input, millions per run (lower is better)',
             {s: tuple(x / 1e6 for x in v) for s, v in agg(runs, 'input_tokens_total').items()}, labels, t, lambda v: f'{v:.2f}M', lower_better=True)
        hbar('output_tokens.png', 'Output tokens', 'What the agent wrote: commands, plans, replies. Thousands per run (lower is better)',
             {s: tuple(x / 1e3 for x in v) for s, v in agg(runs, 'output_tokens').items()}, labels, t, lambda v: f'{v:.0f}K', lower_better=True)
        hbar('cost.png', 'Cost per run', 'US dollars, as reported by Claude Code (lower is better)',
             agg(runs, 'cost_usd'), labels, t, lambda v: f'${v:.2f}', lower_better=True)
        heatmap(runs, labels, t, os.path.join(OUT, 'metrics_heatmap.png'))
    print('charts →', os.path.join(HERE, 'charts'), '| tables →', RES)


if __name__ == '__main__':
    main()
