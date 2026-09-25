"""One-time migration script: builds team_data/teams.json from the four
legacy team-reference files (transform/config/teams.py's
TEAM_NAME_SUBSTITUTIONS, transform/colors.csv, and cfb-grid-web's
teams_lookup.csv). See team_data/README.md for the resulting file's shape
and how to keep it up to date going forward -- this script is not part of
that ongoing workflow, it's kept only as a record of how teams.json was
first derived.

Usage: python team_data/reconcile_one_time.py [--web-repo PATH]
Writes team_data/teams.json and prints a full report to stdout.

NOTE: this script reads transform/colors.csv, which the migration this
script performed went on to delete (colors now live in teams.json/
config/teams.py's TEAM_COLORS). Re-running this script from a checkout
made after that migration will fail on a missing colors.csv -- that's
expected; it isn't meant to be re-run, only kept as a record of how
teams.json was first derived. Retrieve the old colors.csv from git history
if you ever genuinely need to re-run this.
"""

import argparse
import csv
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)  # cfb-grid-data/cfb-grid-data/
TRANSFORM_TEAMS_PY = os.path.join(REPO_ROOT, 'transform', 'config', 'teams.py')
COLORS_CSV = os.path.join(REPO_ROOT, 'transform', 'colors.csv')
DEFAULT_WEB_REPO = os.path.join(REPO_ROOT, '..', '..', 'cfb-grid-web')
TEAMS_LOOKUP_CSV_REL = os.path.join('mern', 'client', 'public', 'teams_lookup.csv')
OUTPUT_JSON = os.path.join(HERE, 'teams.json')


def load_team_name_substitutions():
    spec = importlib.util.spec_from_file_location('_legacy_teams', TRANSFORM_TEAMS_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TEAM_NAME_SUBSTITUTIONS


def load_colors_csv(substitutions):
    """Returns dict: canonical_name -> (espn_id, color), last-row-wins per
    canonical name (post-substitution) -- reproduces load_colors()'s current
    dict-overwrite semantics closely enough to resolve the same way on the
    two known duplicate-row cases (Concord, Morehouse)."""
    result = {}
    with open(COLORS_CSV, encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split(',')
            if len(parts) < 2:
                continue
            raw_name = parts[0]
            if not raw_name:
                continue
            try:
                espn_id = int(os.path.basename(parts[1]).replace('.png', ''))
            except ValueError:
                continue
            color = parts[2] if len(parts) >= 3 and parts[2] else None
            canonical = substitutions.get(raw_name, raw_name)
            result[canonical] = (espn_id, color, raw_name)
    return result


def load_teams_lookup_csv(path):
    """Returns dict: School -> (Id, Color)."""
    result = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            school = (row.get('School') or '').strip()
            if not school:
                continue
            id_str = (row.get('Id') or '').strip()
            if not id_str:
                continue
            color = (row.get('Color') or '').strip() or None
            result[school] = (int(id_str), color)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--web-repo', default=DEFAULT_WEB_REPO)
    args = parser.parse_args()

    substitutions = load_team_name_substitutions()
    colors_by_canonical = load_colors_csv(substitutions)

    teams_lookup_csv = os.path.join(args.web_repo, TEAMS_LOOKUP_CSV_REL)
    if not os.path.isfile(teams_lookup_csv):
        print(f'ERROR: teams_lookup.csv not found at {teams_lookup_csv}', file=sys.stderr)
        sys.exit(1)
    teams_lookup = load_teams_lookup_csv(teams_lookup_csv)

    # groups: espn_id -> {"names": set(), "colors": {}}  (colors: source -> color)
    groups = {}

    def get_group(espn_id):
        return groups.setdefault(espn_id, {'names': set(), 'colors': {}})

    for canonical, (espn_id, color, raw_name) in colors_by_canonical.items():
        g = get_group(espn_id)
        g['names'].add(canonical)
        g['names'].add(raw_name)
        if color:
            g['colors']['colors_csv'] = color

    for school, (espn_id, color) in teams_lookup.items():
        g = get_group(espn_id)
        g['names'].add(school)
        if color:
            g['colors']['teams_lookup'] = color

    # Attach every raw CFBD name to its group via TEAM_NAME_SUBSTITUTIONS,
    # even if that raw name never appeared directly in either source file.
    name_to_group_id = {}
    for espn_id, g in groups.items():
        for name in g['names']:
            name_to_group_id[name] = espn_id

    orphan_raw_names = []
    for raw_name, canonical in substitutions.items():
        if canonical in name_to_group_id:
            gid = name_to_group_id[canonical]
            groups[gid]['names'].add(raw_name)
        elif raw_name in name_to_group_id:
            pass
        else:
            orphan_raw_names.append(raw_name)

    substitution_targets = set(substitutions.values())

    color_mismatches = []
    new_from_colors_only = []
    aliases_folded_from_colors = []
    entries = []

    for espn_id, g in sorted(groups.items()):
        names = g['names']
        colors = g['colors']

        if len(colors) == 2 and colors.get('colors_csv') != colors.get('teams_lookup'):
            color_mismatches.append((espn_id, sorted(names), colors))

        color = colors.get('teams_lookup') or colors.get('colors_csv')

        in_teams_lookup = any(n in teams_lookup for n in names)
        if not in_teams_lookup:
            if espn_id in {gid for gid in groups if any(n in teams_lookup for n in groups[gid]['names'])}:
                aliases_folded_from_colors.append((espn_id, sorted(names)))
            else:
                new_from_colors_only.append((espn_id, sorted(names)))

        substitution_target_names = [n for n in names if n in substitution_targets]
        if substitution_target_names:
            canonical_name = sorted(substitution_target_names)[0]
        else:
            teams_lookup_names = [n for n in names if n in teams_lookup]
            if teams_lookup_names:
                canonical_name = sorted(teams_lookup_names)[0]
            else:
                canonical_name = sorted(names)[0]

        aliases = sorted(n for n in names if n != canonical_name)
        entries.append({
            'canonical_name': canonical_name,
            'espn_id': espn_id,
            'color': color,
            'aliases': aliases,
        })

    entries.sort(key=lambda e: e['canonical_name'])

    all_names_in_output = set()
    for e in entries:
        all_names_in_output.add(e['canonical_name'])
        all_names_in_output.update(e['aliases'])

    missing_canonical_targets = sorted(t for t in substitution_targets if t not in all_names_in_output)

    print('=== Reconciliation report ===')
    print(f'Total teams (groups by espn_id): {len(entries)}')
    print(f'Orphan raw names (in TEAM_NAME_SUBSTITUTIONS, canonical not found in either source): {len(orphan_raw_names)}')
    for n in orphan_raw_names:
        print(f'  - {n!r} -> {substitutions[n]!r}')
    print(f'Color mismatches between colors.csv and teams_lookup.csv (teams_lookup value wins): {len(color_mismatches)}')
    for espn_id, names, colors in color_mismatches:
        print(f'  - id={espn_id} names={names} colors_csv={colors.get("colors_csv")!r} teams_lookup={colors.get("teams_lookup")!r}')
    print(f'New teams from colors.csv only (no teams_lookup.csv coverage at all): {len(new_from_colors_only)}')
    for espn_id, names in new_from_colors_only:
        print(f'  - id={espn_id} names={names}')
    print(f'Aliases folded in from colors.csv (id already existed in teams_lookup.csv under another name): {len(aliases_folded_from_colors)}')
    for espn_id, names in aliases_folded_from_colors:
        print(f'  - id={espn_id} names={names}')
    print(f'MISSING canonical names (substitution target with no color/id coverage at all -- must be 0): {len(missing_canonical_targets)}')
    for n in missing_canonical_targets:
        print(f'  - {n!r}')

    if missing_canonical_targets:
        print('\nERROR: invariant violated, not writing teams.json. Fix coverage gaps above first.', file=sys.stderr)
        sys.exit(1)

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
        f.write('\n')

    print(f'\nWrote {OUTPUT_JSON} ({len(entries)} teams).')


if __name__ == '__main__':
    main()
