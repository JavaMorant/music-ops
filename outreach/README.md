# outreach

A small booking CRM for a solo artist: a SQLite pipeline of promoters and venues,
and a drafter that turns each contact into a **personalised email draft on disk**.

> **Hard rule — it never sends.** `outreach draft` writes a markdown file for you
> to review, edit, and send by hand. There is no send command, no SMTP, no mail
> client anywhere in the code — pinned by `tests/test_never_sends.py`. Personal,
> researched outreach only; no bulk blasting.

> Status: schema + pipeline + drafting are built and tested (the MVP from the
> build order). Not yet built: the ambition-tier **research step** (enrich a
> contact with real, specific detail before drafting — still drafts only).

## Install

```bash
cd outreach
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

The core depends only on Typer (SQLite, CSV and TOML are stdlib).

## The pipeline

Every contact moves through these stages; the last two are terminal (no follow-up
is ever owed on them):

```
lead → contacted → replied → negotiating → booked
                                          ↘ dead
```

## Commands

```bash
# add a contact (starts as a 'lead', due for first contact now)
outreach add "Sam Promoter" --org "Corsica Studios" --city London \
    --role "talent buyer" --capacity 500 --genre-fit "amapiano into UK garage" \
    --source "Resident Advisor" --notes "books Friday warehouse nights"

outreach list                       # whole pipeline, ordered down the funnel
outreach list --stage contacted     # one stage
outreach due                        # follow-ups owed today (your action list)

# write a personalised draft FILE (never sends)
cp outreach-profile.example.toml outreach-profile.toml   # fill in your name/links once
outreach draft 1                                          # → drafts/<date>-sam-promoter-cold.md
outreach draft 1 --template followup                     # a nudge instead

# after you send it yourself, record the touch
outreach log 1 "sent intro email" --channel email --stage contacted
outreach log 1 "they replied, keen" --stage replied --followup-in 3
outreach log 1 "confirmed for 14 Aug" --stage booked     # clears the follow-up

outreach import contacts.csv        # bulk-add (CSV with a 'name' column)
outreach export backup.csv          # back the pipeline up
```

Every command has `--help`. The database defaults to `./outreach.db` (`--db` to
override); drafts go to `./drafts/`.

## Web app

A local single-page CRM over the same engine — a drag-between-stages pipeline
board, add/log/draft in the browser, and a viewer for the drafts on disk. Still
drafts only; there is no send route anywhere.

```bash
pip install -e '.[web]'
outreach web --profile outreach-profile.toml   # → http://127.0.0.1:8011
```

Bound to localhost; it shares the same `--db` and `--out` (drafts dir) as the
CLI, so the board and `outreach list` are the same pipeline.

## Drafting

Templates are plain markdown with a leading `Subject:` line and `{{ slot }}`
placeholders (packaged: `cold`, `followup`; or pass a path to your own). Slots
the contact data or your profile can fill are substituted in; anything we don't
know — and the specific researched detail every good cold email needs — is left
as a visible `[[ FILL: … ]]` marker. The tool personalises from real data and
**flags the rest rather than guessing.** Your name, tagline, email and links come
from `outreach-profile.toml` (see `outreach-profile.example.toml`).

## Tests

```bash
pytest            # pipeline engine, drafting, the never-sends invariant, CLI
```
