# team_data

Single canonical source for every team's display name, known alternate
spellings, ESPN/CFBD numeric id (used for logo filenames), and hex color.
Everything else that needs this data — `transform/config/teams.py`,
`live-scores/config/teams.py`, and `cfb-grid-web`'s
`public/teams_lookup.json` — is *generated* from `teams.json` here, not
hand-maintained separately. This directory itself is never deployed (it
lives outside both `transform/`'s and `live-scores/`'s Docker build
contexts) — only the files `generate.py` writes into those directories ship.

## Why one file

A team's canonical name, id, color, and known alias spellings (raw CFBD
names that need normalizing, plus alternate display spellings) are all
facts about the same entity. Before this consolidation they were split
across a Python dict (duplicated verbatim in two services) and two
different CSVs with different keys and different name conventions for the
same ~740 teams — see the git history of this directory's addition for the
full reconciliation writeup.

## `teams.json`

A JSON array, one object per team:

```json
{
  "canonical_name": "Air Force",
  "espn_id": 2005,
  "color": "#003087",
  "aliases": []
}
```

`aliases` holds every other known spelling for the team — raw CFBD names
that need normalizing to the canonical display name, and/or extra display
spellings the frontend's schedule-popup lookup needs. There's no
per-alias "used for X" tag; an alias that's never looked up by a given
consumer is simply never queried, so it's harmless to list broadly.

## Making a change

**Never hand-edit `teams.json` directly** — use `edit.py`, which keeps the
file's formatting stable and checks for name/id collisions:

```bash
python team_data/edit.py show --name "Air Force"                     # look up a team by any known spelling
python team_data/edit.py set-color --name "Air Force" --color '#003087'
python team_data/edit.py set-logo-id --name "Air Force" --espn-id 2005
python team_data/edit.py rename-canonical --name "App State" --new-name "Appalachian St."
python team_data/edit.py add-alias --name "Air Force" --alias "AFA"
python team_data/edit.py remove-alias --alias "AFA"
python team_data/edit.py add-team --canonical-name "New School" --espn-id 99999 --color '#112233' --alias "New School Raw Name"
python team_data/edit.py list-missing                                 # teams with no color and/or no id
```

If you're chatting with Claude and notice a missing logo, a missing color,
or a display name that's too long for the grid, just say which team and
what's wrong — Claude can run the right `edit.py` command for you.

After any edit, propagate it and review the diffs before committing/
deploying:

```bash
python team_data/generate.py            # writes the derived files in all 3 repos
python team_data/generate.py --check    # dry run -- reports what would change, exits non-zero on drift
git diff                                # review within cfb-grid-data
cd ../../cfb-grid-web && git diff       # review within cfb-grid-web
```

Each repo still commits/deploys independently per its own normal process
(see the top-level workspace `CLAUDE.md`) — `generate.py` only writes the
files, it doesn't commit or deploy anything itself.

## Files in this directory

- `teams.json` — the canonical source, described above.
- `edit.py` — the CLI for changing `teams.json` (see above).
- `generate.py` — regenerates every derived file from `teams.json`. Run
  after any `edit.py` command, or after a direct edit to `teams.json`.
- `reconcile_one_time.py` — the one-time migration script that first built
  `teams.json` out of the four files it replaced. Not part of the ongoing
  workflow; kept only as a record of how the initial data was derived.

## Invariant

Every team's alias/canonical names must be globally unique across the
whole file (no two teams sharing a name). `generate.py` checks this (and
fails loudly, non-zero exit, if violated) every time it runs, whether or
not `--check` is passed.
