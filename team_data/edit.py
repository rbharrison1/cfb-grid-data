"""The sanctioned way to modify team_data/teams.json day-to-day -- covers
the recurring cases (missing logo/id, missing color, a display name that
needs shortening to fit the grid) without ever hand-touching the JSON file.

After any mutating subcommand, run `python team_data/generate.py` to
propagate the change to transform/, live-scores/, and cfb-grid-web, then
review the diffs before committing/deploying each repo per its own normal
process (see README.md).

Usage:
    python team_data/edit.py show --name NAME
    python team_data/edit.py set-color --name NAME --color '#RRGGBB'
    python team_data/edit.py set-logo-id --name NAME --espn-id ID
    python team_data/edit.py rename-canonical --name NAME --new-name NEW
    python team_data/edit.py add-alias --name NAME --alias ALIAS
    python team_data/edit.py remove-alias --alias ALIAS
    python team_data/edit.py add-team --canonical-name NAME --espn-id ID --color '#RRGGBB' [--alias ALIAS ...]
    python team_data/edit.py list-missing

NAME/ALIAS lookups resolve against a team's canonical_name or any of its
current aliases -- you can look a team up by whatever spelling you have on
hand.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEAMS_JSON = os.path.join(HERE, 'teams.json')

REMINDER = ('\nRun `python team_data/generate.py` to propagate this change, '
            'then review the diffs before committing/deploying each repo.')


def load():
    with open(TEAMS_JSON, encoding='utf-8') as f:
        return json.load(f)


def save(teams):
    teams.sort(key=lambda t: t['canonical_name'])
    with open(TEAMS_JSON, 'w', encoding='utf-8') as f:
        json.dump(teams, f, indent=2, ensure_ascii=False)
        f.write('\n')


def all_names(team):
    return [team['canonical_name'], *team['aliases']]


def find(teams, name):
    for t in teams:
        if name in all_names(t):
            return t
    return None


def find_or_die(teams, name):
    t = find(teams, name)
    if t is None:
        print(f'No team found matching {name!r}. Use `show --name ...` to search, '
              f'or `add-team` to create a new one.', file=sys.stderr)
        sys.exit(1)
    return t


def check_name_available(teams, name, excluding=None):
    owner = find(teams, name)
    if owner is not None and owner is not excluding:
        print(f'ERROR: {name!r} is already used by {owner["canonical_name"]!r} '
              f'(espn_id={owner["espn_id"]}).', file=sys.stderr)
        sys.exit(1)


def cmd_show(args, teams):
    t = find_or_die(teams, args.name)
    print(json.dumps(t, indent=2, ensure_ascii=False))


def cmd_set_color(args, teams):
    t = find_or_die(teams, args.name)
    old = t['color']
    t['color'] = args.color
    save(teams)
    print(f'{t["canonical_name"]}: color {old!r} -> {args.color!r}{REMINDER}')


def cmd_set_logo_id(args, teams):
    t = find_or_die(teams, args.name)
    other = next((x for x in teams if x is not t and x['espn_id'] == args.espn_id), None)
    if other is not None:
        print(f'WARNING: espn_id {args.espn_id} is already used by {other["canonical_name"]!r} -- '
              f'both teams will now share it.', file=sys.stderr)
    old = t['espn_id']
    t['espn_id'] = args.espn_id
    save(teams)
    print(f'{t["canonical_name"]}: espn_id {old!r} -> {args.espn_id!r}{REMINDER}\n'
          f'Note: this only changes the mapping, not image assets -- if '
          f'cfb-grid-web/mern/client/public/logos/{args.espn_id}.png does not already exist, '
          f'add it separately or the logo will not render.')


def cmd_rename_canonical(args, teams):
    t = find_or_die(teams, args.name)
    check_name_available(teams, args.new_name, excluding=t)
    old_canonical = t['canonical_name']
    aliases = [a for a in t['aliases'] if a != args.new_name]
    if old_canonical not in aliases:
        aliases.append(old_canonical)
    t['canonical_name'] = args.new_name
    t['aliases'] = sorted(aliases)
    save(teams)
    print(f'Renamed {old_canonical!r} -> {args.new_name!r} '
          f'({old_canonical!r} kept as an alias){REMINDER}')


def cmd_add_alias(args, teams):
    t = find_or_die(teams, args.name)
    check_name_available(teams, args.alias, excluding=t)
    t['aliases'] = sorted({*t['aliases'], args.alias})
    save(teams)
    print(f'Added alias {args.alias!r} to {t["canonical_name"]!r}{REMINDER}')


def cmd_remove_alias(args, teams):
    t = next((x for x in teams if args.alias in x['aliases']), None)
    if t is None:
        print(f'ERROR: {args.alias!r} is not a known alias of any team.', file=sys.stderr)
        sys.exit(1)
    t['aliases'] = [a for a in t['aliases'] if a != args.alias]
    save(teams)
    print(f'Removed alias {args.alias!r} from {t["canonical_name"]!r}{REMINDER}')


def cmd_add_team(args, teams):
    check_name_available(teams, args.canonical_name)
    for a in args.alias:
        check_name_available(teams, a)
    other = next((x for x in teams if x['espn_id'] == args.espn_id), None)
    if other is not None:
        print(f'WARNING: espn_id {args.espn_id} is already used by {other["canonical_name"]!r} -- '
              f'both teams will now share it.', file=sys.stderr)
    teams.append({
        'canonical_name': args.canonical_name,
        'espn_id': args.espn_id,
        'color': args.color,
        'aliases': sorted(args.alias),
    })
    save(teams)
    print(f'Added new team {args.canonical_name!r} (espn_id={args.espn_id}){REMINDER}')


def cmd_list_missing(args, teams):
    missing = [t for t in teams if not t.get('color') or not t.get('espn_id')]
    if not missing:
        print('No teams with a missing color or espn_id.')
        return
    for t in sorted(missing, key=lambda t: t['canonical_name']):
        gaps = []
        if not t.get('color'):
            gaps.append('color')
        if not t.get('espn_id'):
            gaps.append('espn_id')
        print(f'{t["canonical_name"]} (espn_id={t.get("espn_id")}): missing {", ".join(gaps)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('show')
    p.add_argument('--name', required=True)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser('set-color')
    p.add_argument('--name', required=True)
    p.add_argument('--color', required=True)
    p.set_defaults(func=cmd_set_color)

    p = sub.add_parser('set-logo-id')
    p.add_argument('--name', required=True)
    p.add_argument('--espn-id', required=True, type=int)
    p.set_defaults(func=cmd_set_logo_id)

    p = sub.add_parser('rename-canonical')
    p.add_argument('--name', required=True)
    p.add_argument('--new-name', required=True)
    p.set_defaults(func=cmd_rename_canonical)

    p = sub.add_parser('add-alias')
    p.add_argument('--name', required=True)
    p.add_argument('--alias', required=True)
    p.set_defaults(func=cmd_add_alias)

    p = sub.add_parser('remove-alias')
    p.add_argument('--alias', required=True)
    p.set_defaults(func=cmd_remove_alias)

    p = sub.add_parser('add-team')
    p.add_argument('--canonical-name', required=True)
    p.add_argument('--espn-id', required=True, type=int)
    p.add_argument('--color', required=True)
    p.add_argument('--alias', action='append', default=[])
    p.set_defaults(func=cmd_add_team)

    p = sub.add_parser('list-missing')
    p.set_defaults(func=cmd_list_missing)

    args = parser.parse_args()
    teams = load()
    args.func(args, teams)


if __name__ == '__main__':
    main()
