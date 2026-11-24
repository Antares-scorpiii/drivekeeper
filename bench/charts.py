#!/usr/bin/env python3
"""Summary tables, per-skill reports and charts (light and dark PNG) from bench/results/*.score.json.

  charts.py   for each dataset: bench/results/<dataset>/{summary.md, summary.csv, reports/<skill>.md, charts/{light,dark}/}
              plus bench/results/versions/{light,dark}/versions.png (Drivekeeper releases on VERSIONS_DATASET)

Form choices (dataviz method): one quantity per chart; skills are named on the axis, so colour is not needed for
identity: Drivekeeper in Claude clay, the others warm grey, every bar labelled with its value. The heatmap uses one
sequential clay ramp with a value in every cell. The releases chart is the one exception to "one quantity": time
(bars, left axis) and cost (line, right axis) share the zero baseline, with the bars scaled to stay below the line.
Whiskers show min-max when a skill has several runs on a dataset. Runs cut short (is_error) are skipped.
Look: Claude's warm surfaces, a light hand-drawn wobble (matplotlib path.sketch) and Patrick Hand (OFL) if it is in
bench/fonts/. Runs with another model or effort (e.g. drivekeeper@opus-5-5-low) are plotted as their own rows.
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
VERSIONS_DATASET = 'family-pc'
THEMES = {   # validated with the dataviz validator: clay vs grey passes CVD (ΔE 15.6 light / 9.5 dark) and lightness band
    'light': dict(surface='#faf9f5', ink='#141413', ink2='#5e5d59', muted='#87867f', grid='#e8e6dc', accent='#c6613f', blue='#2f6fb3',
                  other='#b0aea5', ramp=['#f9ebe4', '#f2cdbb', '#e9a98c', '#d97757', '#b4532f', '#7d3519']),
    'dark': dict(surface='#262624', ink='#faf9f5', ink2='#c2c0b6', muted='#87867f', grid='#3a3a37', accent='#d4704f', blue='#5b8fd6',
                 other='#6b6a65', ramp=['#3a2b25', '#5e3626', '#8c4a30', '#c0623e', '#e08a6d', '#f2c4b0']),
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


def load(dataset):
    """Competitor labels and, per skill, the metrics of every finished run on this dataset."""
    labels = {c['id']: c for c in json.load(open(os.path.join(HERE, 'competitors.json')))}
    runs = collections.defaultdict(list)
    for f in sorted(glob.glob(os.path.join(RES, f'*.{dataset}.score.json'))):
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
    """Per skill: (mean, min, max) of one metric."""
    out = {}
    for s, ms in runs.items():
        v = [m.get(key) for m in ms if isinstance(m.get(key), (int, float))]
        if v:
            out[s] = (sum(v) / len(v), min(v), max(v))
    return out


def flat(fig, ax):
    """Keep figure and axes backgrounds straight (no hand-drawn wobble)."""
    for p in (fig.patch, ax.patch):   # keep backgrounds straight so the wobble never exposes the page edge
        p.set_sketch_params(None)


def style(ax, t):
    """Surface, spines, ticks and grid for one axes."""
    ax.set_facecolor(t['surface'])
    flat(ax.figure, ax)
    for side in ('top', 'right', 'left'):
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(t['muted'])
    ax.tick_params(colors=t['ink2'], labelsize=12, length=0)
    ax.grid(axis='x', color=t['grid'], linewidth=0.8)
    ax.set_axisbelow(True)


def name(labels, s):
    """Axis label for a skill: our skill starred, model tag and install count added."""
    base, _, cfg = s.partition('@')                                    # e.g. drivekeeper@opus-5-5-low
    c = labels.get(base, {})
    tag = f" ({c['installs']} installs)" if c.get('installs') else ''
    model = ' · ' + re.sub(r'(\d)-(\d)', r'\1.\2', cfg).replace('-', ' ').title() if cfg else ''   # Opus 5.5 Low
    return (f'{base} ★' if base == OURS else base) + model + tag


def hbar(fig_name, title, subtitle, data, labels, t, fmt, xmax=None, lower_better=False, color_fn=None):
    """One horizontal bar chart, best at the top, our skill in clay, values printed on the bars."""
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
    """Skills × rate metrics, one sequential clay ramp, value in every cell."""
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
    fig.text(0.012, 0.985 - 0.32 / fig.get_figheight(), f'{DATASET} dataset, one Sonnet 5.5 run per skill; ' + ('darker' if t is THEMES['light'] else 'brighter') + ' = better',
             ha='left', va='top', fontsize=12, color=t['ink2'])
    fig.tight_layout(rect=(0, 0, 1, 1 - 0.55 / fig.get_figheight()))
    fig.savefig(path, dpi=160, facecolor=t['surface'])
    plt.close(fig)


def tables(runs, labels, out):
    """summary.md and summary.csv for one dataset."""
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
    with open(os.path.join(out, 'summary.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    pct = lambda v: '' if v == '' else f'{v*100:.0f}%'
    md = ['| Skill | Safety | Organisation | Files lost | Placement | Dedup | Junk cleared | Sets kept together | Projects intact | Ignored injection | Time (min) | Turns | Input tokens | Output tokens | Cost |',
          '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        skill = f"**{r['skill']}**" if r['skill'].startswith(OURS) else r['skill']
        minutes = '' if r['duration_s'] == '' else round(r['duration_s'] / 60, 1)
        mtok = lambda v: '' if v == '' else f'{v / 1e6:.2f}M'
        ktok = lambda v: '' if v == '' else f'{v / 1e3:.0f}K'
        num = lambda v, d=1: '' if v == '' else (f'{v:.0f}' if float(v).is_integer() else f'{v:.{d}f}')
        md.append(f"| {skill} | {r['safety_score']:.1f} | {r['organisation_score']:.1f} | {num(r['critical_lost'])} | "
                  f"{pct(r['placement_accuracy'])} | {pct(r['dedup_rate'])} | {pct(r['junk_cleared'])} | "
                  f"{pct(r['together_integrity'])} | {pct(r['project_integrity'])} | {pct(r['injection_resisted'])} | {minutes} | {num(r['turns'])} | {mtok(r['input_tokens_total'])} | {ktok(r['output_tokens'])} | {'' if r['cost_usd'] == '' else f"${r['cost_usd']:.2f}"} |")
    open(os.path.join(out, 'summary.md'), 'w').write('\n'.join(md) + '\n')


def reports(dataset, runs, out):
    """Each skill's final report to the user, verbatim, under the scorer's key numbers."""
    os.makedirs(os.path.join(out, 'reports'), exist_ok=True)
    for skill, ms in runs.items():
        m = ms[0]
        rid = f'{skill}.{dataset}'
        result = json.load(open(os.path.join(RES, f'{rid}.claude.json'))).get('result', '')
        detail = json.load(open(os.path.join(RES, f'{rid}.score.json')))['detail']
        found = [f'- {k.replace("_", " ")}: {", ".join(map(str, v))}' for k, v in detail.items()
                 if k in ('lost', 'project_broken', 'together_broken', 'game_broken', 'app_broken') and v]
        with open(os.path.join(out, 'reports', f'{skill}.md'), 'w') as f:
            f.write(f'# {skill} on the `{dataset}` dataset\n\n'
                    '| Safety | Organisation | Files lost | Time | Turns | Input tokens | Output tokens | Cost |\n'
                    '|---|---|---|---|---|---|---|---|\n'
                    f"| {m['safety_score']} | {m['organisation_score']} | {m['critical_lost']} | {m['duration_s'] / 60:.1f} min | "
                    f"{m['turns']} | {m['input_tokens_total'] / 1e6:.2f}M | {m['output_tokens'] / 1e3:.0f}K | ${m['cost_usd']:.2f} |\n\n"
                    '**Problems found by the scorer:**\n' + ('\n'.join(found) if found else 'none') + '\n\n'
                    '## Final report from the agent (verbatim)\n\n' + result.strip() + '\n')


def tokens(dataset, runs, t, path):
    """Back-to-back bars, one row per skill, cheapest at the top: input tokens grow left (clay), output tokens grow
    right (blue, own scale: output is ~2% of input), cost as a column of figures. The divide sits at the image centre."""
    latest = max((s for s in runs if s.startswith(OURS + '-v')), default=None)
    cost = {s: v[0] for s, v in agg(runs, 'cost_usd').items()}
    tin = {s: v[0] / 1e6 for s, v in agg(runs, 'input_tokens_total').items()}
    tout = {s: v[0] / 1e3 for s, v in agg(runs, 'output_tokens').items()}
    order = sorted(cost, key=cost.get)           # cheapest at the top
    n = len(order)
    fig, (axl, axr) = plt.subplots(1, 2, figsize=(8.6, 0.48 * n + 1.9), facecolor=t['surface'], sharey=True,
                                   )
    for ax in (axl, axr):
        style(ax, t)
        ax.spines['bottom'].set_visible(False)
        ax.set_xticks([])
        ax.set_clip_on(False)
        ax.grid(False)
    mi, mo = max(tin.values()), max(tout.values())
    for i, s in enumerate(order):
        axl.barh(i, tin[s], height=0.52, color=t['accent'], edgecolor=t['surface'], linewidth=2)
        axr.barh(i, tout[s], height=0.52, color=t['blue'], edgecolor=t['surface'], linewidth=2)
        axl.text(tin[s] + mi * 0.02, i, f'{tin[s]:.2f}M', ha='right', va='center', color=t['ink2'], fontsize=10)
        axr.text(tout[s] + mo * 0.02, i, f'{tout[s]:.0f}K', ha='left', va='center', color=t['ink2'], fontsize=10)
        axr.text(mo * 1.85, i, f'${cost[s]:.2f}', ha='right', va='center', color=t['ink'], fontsize=13,
                 fontweight='bold' if s.startswith(OURS) else 'normal')
    axl.set_xlim(mi * 1.25, 0)                                  # input grows to the left
    axr.set_xlim(0, mo * 1.25)
    axl.set_yticks(range(n), [s.replace(OURS + '-', OURS + ' ') + (' ★' if s == latest else '') for s in order])
    for lbl in axl.get_yticklabels():
        if lbl.get_text().startswith(OURS):
            lbl.set_color(t['ink']); lbl.set_fontweight('bold')
    axr.tick_params(left=False)
    top = -0.75
    axl.text(mi * 0.02, top, 'Input tokens (millions)', ha='right', va='center', color=t['accent'], fontsize=12, fontweight='bold')
    axr.text(mo * 0.02, top, 'Output tokens (thousands)', ha='left', va='center', color=t['blue'], fontsize=12, fontweight='bold')
    axr.text(mo * 1.85, top, 'Cost', ha='right', va='center', color=t['ink'], fontsize=12, fontweight='bold')
    axl.set_ylim(n - 0.4, -1.05)                                # shared y: set once, top row first
    fig.text(0.058, 0.985, 'Tokens and cost per run', ha='left', va='top', fontsize=17, fontweight='bold', color=t['ink'])
    fig.text(0.058, 0.985 - 0.32 / fig.get_figheight(), f'Dataset {dataset}, Sonnet 5.5. Cheapest at the top; lower is better everywhere',
             ha='left', va='top', fontsize=12, color=t['ink2'])
    fig.subplots_adjust(left=0.2, right=0.8, bottom=0.03, top=1 - 0.7 / fig.get_figheight(), wspace=0.03)   # centre gap at x=0.5
    fig.savefig(path, dpi=160, facecolor=t['surface'], bbox_inches='tight', pad_inches=0.08)
    plt.close(fig)


def versions(dataset, runs, out):
    """Drivekeeper releases on one dataset: time as bars (left axis), cost as a line (right axis). Both axes start
    at 0 on the same baseline; the bar axis runs to twice the longest time, so the bars stay below the line.
    Written to <out>/{light,dark}/versions.png."""
    vs = sorted(s for s in runs if s.startswith(OURS + '-v'))
    cost = [agg(runs, 'cost_usd')[s][0] for s in vs]
    mins = [agg(runs, 'duration_s')[s][0] / 60 for s in vs]
    x = list(range(len(vs)))
    top_m, top_c = max(mins) * 2, max(cost) * 1.25
    for mode, t in THEMES.items():
        fig, ax = plt.subplots(figsize=(8.2, 5.4), facecolor=t['surface'])
        style(ax, t)
        ax.grid(axis='x', visible=False)
        ax.grid(axis='y', visible=False)
        ax.bar(x, mins, width=0.5, color=t['accent'], edgecolor=t['surface'], linewidth=2, label='Time per run (bars, left)')
        for i, m in zip(x, mins):
            ax.text(i, m / 2, f'{m:.1f} min', ha='center', va='center', color=t['surface'], fontsize=13, fontweight='bold')
        ax.set_ylim(0, top_m)
        ax.set_ylabel('Minutes per run', color=t['ink2'], fontsize=12)
        ax.set_xticks(x, [s.split('-', 1)[1] for s in vs], fontsize=14)
        ax2 = ax.twinx()
        flat(fig, ax2)
        for side in ax2.spines:
            ax2.spines[side].set_visible(False)
        ax2.tick_params(colors=t['ink2'], labelsize=12, length=0)
        ax2.set_ylim(0, top_c)                               # same zero as the bars
        ax2.yaxis.set_major_formatter(lambda v, _: f'${v:.1f}')
        ax2.set_ylabel('Cost per run', color=t['ink2'], fontsize=12)
        ax2.plot(x, cost, color=t['ink'], linewidth=2, marker='o', markersize=9,
                 markeredgecolor=t['surface'], markeredgewidth=2, label='Cost per run (line, right)')
        for i, c in zip(x, cost):
            ax2.text(i, c + top_c * 0.04, f'${c:.2f}', ha='center', va='bottom', color=t['ink'], fontsize=13, fontweight='bold')
        h, l = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h + h2, l + l2, loc='upper center', bbox_to_anchor=(0.5, -0.09), ncol=2, frameon=False,
                  labelcolor=t['ink2'], fontsize=12)
        dc, dm = (1 - cost[-1] / cost[0]) * 100, (1 - mins[-1] / mins[0]) * 100
        fig.text(0.012, 0.985, 'Drivekeeper releases: time and cost per run', ha='left', va='top', fontsize=17,
                 fontweight='bold', color=t['ink'])
        fig.text(0.012, 0.925, f'Dataset {dataset}, Sonnet 5.5. {vs[0][len(OURS) + 1:]} → {vs[-1][len(OURS) + 1:]}: '
                 f'{dm:.0f}% faster, {dc:.0f}% cheaper, safety 100 throughout', ha='left', va='top', fontsize=12, color=t['ink2'])
        fig.tight_layout(rect=(0, 0, 1, 0.89))
        os.makedirs(os.path.join(out, mode), exist_ok=True)
        fig.savefig(os.path.join(out, mode, 'versions.png'), dpi=160, facecolor=t['surface'])
        plt.close(fig)


def main():
    """Draw every dataset found in bench/results, then the releases chart."""
    datasets = sorted({os.path.basename(f).split('.')[-3] for f in glob.glob(os.path.join(RES, '*.score.json'))})
    if not datasets:
        raise SystemExit('no results yet: run run_bench.py first')
    for ds in datasets:
        draw(ds)
    labels, runs = load(VERSIONS_DATASET)            # the one dataset every release ran on
    versions(VERSIONS_DATASET, runs, os.path.join(RES, 'versions'))


def draw(dataset):
    """Tables, reports and all light and dark charts for one dataset."""
    global DATASET
    DATASET = dataset
    labels, runs = load(dataset)
    out = os.path.join(RES, dataset)
    os.makedirs(out, exist_ok=True)
    tables(runs, labels, out)
    reports(dataset, runs, out)
    for mode, t in THEMES.items():
        global OUT
        OUT = os.path.join(out, 'charts', mode)
        os.makedirs(OUT, exist_ok=True)
        pct = lambda v: f'{v:.0f}'
        hbar('safety.png', 'Safety score', 'Files kept, look-alikes kept, projects/games/apps intact, planted instruction ignored (0–100)',
             agg(runs, 'safety_score'), labels, t, pct, xmax=112)
        hbar('organisation.png', 'Organisation score', 'Right folder, duplicates collapsed, junk cleared, sets kept together (0–100)',
             agg(runs, 'organisation_score'), labels, t, pct, xmax=112)
        hbar('time.png', 'Wall-clock time', 'Minutes per run (lower is better)',
             {s: tuple(x / 60 for x in v) for s, v in agg(runs, 'duration_s').items()}, labels, t, lambda v: f'{v:.1f} min', lower_better=True)
        hbar('input_tokens.png', 'Input tokens', 'Fresh + cached input, millions per run (lower is better)',
             {s: tuple(x / 1e6 for x in v) for s, v in agg(runs, 'input_tokens_total').items()}, labels, t, lambda v: f'{v:.2f}M', lower_better=True)
        hbar('output_tokens.png', 'Output tokens', 'What the agent wrote: commands, plans, replies. Thousands per run (lower is better)',
             {s: tuple(x / 1e3 for x in v) for s, v in agg(runs, 'output_tokens').items()}, labels, t, lambda v: f'{v:.0f}K', lower_better=True)
        hbar('cost.png', 'Cost per run', 'US dollars, as reported by Claude Code (lower is better)',
             agg(runs, 'cost_usd'), labels, t, lambda v: f'${v:.2f}', lower_better=True)
        heatmap(runs, labels, t, os.path.join(OUT, 'metrics_heatmap.png'))
        tokens(dataset, runs, t, os.path.join(OUT, 'tokens.png'))
    print(f'{dataset}: {len(runs)} skills → {out}/')


if __name__ == '__main__':
    main()
