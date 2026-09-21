# mpg-ia-bench

A fantasy football engine reproducing the Mon Petit Gazon mechanics: private leagues,
closed-bid mercato, weekly lineups, bonuses, and game week resolution with real and
virtual goals.

Player ratings are **not** recomputed. The public MPG API serves the official rating
per player and per match, along with the quotations; the engine consumes them.

```
python -m mpg.cli scenarios
```

```
  [ok ] 1. balanced match, crossed bonuses                   5 - 4   (expected 5 - 4)
  [ok ] 2. maximal gap, at home                              10 - 0  (expected 10 - 0)
          virtual scorers: Nuno Mendes, Vitinha, Morton, Doué, Openda
  [ok ] 2b. the same match away                              0 - 9   (expected 0 - 9)
  [ok ] 3. phantom players, watertight goalkeeper            11 - 0  (expected 11 - 0)
  [ok ] 5. MPG save and Valise both bite                     3 - 4   (expected 3 - 4)
```

## Status

All six milestones are implemented, and the thirteen acceptance scenarios of the spec
pass: the five game week scenarios and the eight mercato ones.

| Milestone | Content | Status |
|---|---|---|
| 1 | API ingestion and reference model | done |
| 2 | Game week resolution engine | done — scenarios 1 to 5 green |
| 3 | Leagues, participants, fixture list | done |
| 4 | Closed-bid mercato | done — M1 to M8 green |
| 5 | Lineups, bonuses, substitutions | done |
| 6 | Standings and results | done |

119 tests, ~5 000 lines of source and ~2 500 of tests.

## Quick start

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

The engine and the mercato algorithms are pure, so the whole suite runs with no
database and no network:

```bash
.venv/bin/python -m pytest -q
```

For the API and the ingestion, bring up PostgreSQL 16 and apply the migrations:

```bash
docker compose up -d db
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/python -m mpg.cli ingest --championship 1
.venv/bin/uvicorn mpg.api.app:app --reload
```

The API is the v1 interface: there is no front end. Everything is exercisable from
`/docs`.

```bash
.venv/bin/python -m mpg.scheduler   # ingestion and mercato deadlines
```

## Architecture

```
src/mpg/
  engine/        pure resolution engine -- no database, no framework, no I/O
    lines.py           lines of play, ultra-positions, the gauntlet
    models.py          dataclasses in, MatchResult out
    formations.py      the eight formations and their slot structure
    bonuses.py         bonus and malus arithmetic, Miroir resolution
    substitutions.py   tactical, mandatory and live changes, then Rotaldos
    mpg_goals.py       crossing the opposing lines
    resolver.py        the eight steps of the resolution, in order
    flags.py           every undocumented rule, switchable
  mercato/       closed bids
    rules.py           squad quota and bid-entry checks (pure)
    allocation.py      awarding players, seeded and reproducible (pure)
    random_bids.py     bids for whoever never validated (pure)
    service.py         the database-facing lifecycle
  db/            SQLAlchemy 2.x model
  ingest/        public MPG API client, parsers, jobs
  services/      leagues, fixture list, lineups, game weeks, standings
  api/           FastAPI: auth, leagues, mercato, team, matches
```

The engine takes objects in and hands a result back. That is what makes the acceptance
scenarios runnable as tests rather than as scripts, and it is why `tests/test_flags.py`
can replay the same fixture under two readings of a rule and compare.

## The resolution, step by step

The order is the whole game (spec 3.6):

1. resolve the Miroirs
2. per side: own bonuses and the opposing Suarez, then tactical changes, then mandatory
   ones, then phantom players fill what is left
3. the opposing Cheat Code lands on the final XI's field players
4. count the real goals
5. count the virtual ones
6. turn phantom players into conceded own goals
7. arm the MPG save and the Valise
8. work out the scores

### Invariants that carry the weight

Each of these came out of a bug or a close reading, and each has a test:

- **The goalkeeper slot is watertight both ways.** A keeper never replaces a field
  player and a field player never replaces a keeper. Without it, a bench down to one
  goalkeeper plugs holes in defence, the phantom count is wrong, and the punishment own
  goal never lands (scenario 3).
- **The MPG save only ever takes a real goal**, and never the whole total.
- **The Valise takes a real or a virtual goal, never the own goal the phantoms concede.**
- **The Cheat Code lands after the substitutions**, on the final sheet, never on a keeper.
- **A tactical threshold reads the rating with bonuses**, Cheat Code excluded.
- **The Capitaine is lost when its bearer is also the McDo target.**
- **Line averages are computed on the final XI**, after substitutions, phantoms and bonuses.
- **An empty line is crossed without a duel and without a decrement.**
- **Ratings are clamped to [1, 10].**

Comparisons in the gauntlet are exact float comparisons, deliberately. Ratings and
bonuses are all multiples of 0.5 and line averages divide such a sum by at most 5, so
every value a tie can land on is exactly representable in binary floating point. An
epsilon here would turn genuine near-misses into ties and break the twelfth-man rule —
the one that makes Nuno Mendes score at 4.00 against a keeper at 4.00 at home, and miss
the same duel away.

## The mercato

Bids are closed. Two properties matter more than the rest:

- **The iteration over players is sorted by id**, so two runs with the same seed cannot
  diverge once a participant wins several players.
- **The lock is taken on the league, not on the bids.** A resolution is a global
  operation, and the cron firing at the deadline must serialise against the last
  participant validating in the same millisecond. Idempotence rides on `resolved_at`.

Each round carries a `seed`, which makes every draw reproducible — a resolution can be
replayed identically for a dispute or a test (M2).

Nothing is debited until a round resolves, so a losing bid never costs anything, and the
per-round cap (the sum of a participant's bids may not exceed their budget) is what keeps
a participant from winning more players than they can pay for (M3).

### Bid opacity

`tests/test_api_mercato_opacity.py` does not check a handful of routes by hand: it walks
every GET endpoint the OpenAPI schema publishes, calls each as a participant who bid on
nothing, and asserts no response carries a rival's amount. It also checks that a player
under three bids looks byte-for-byte like a player under none, and that a rival's
remaining budget — a direct read on what they can still bid — stays hidden while the
mercato runs.

## Undocumented rules, and how to change your mind

MPG documents none of the following. Each sits behind a flag with the spec's default,
and each has a test that runs the fixture both ways (`tests/test_flags.py`):

| Flag | Default | The other reading |
|---|---|---|
| `mandatory_sub_direction` | `nearest` | `lower` — only pull players from further forward |
| `mpg_save_can_cancel_own_goal` | `True` | the save is confined to ordinary goals |
| `save_before_valise` | `True` | the Valise resolves first |
| `goals_cancelled_per_valise` | `1` | more than one |
| `valise_can_cancel_real_own_goal` | `True` | follow the bonus table, which spares own goals |
| `out_of_position_line` | `slot` | `player` — the substitute keeps his own line |
| `mirror_steals_limited_bonus` | `True` | the Miroir does nothing |
| `MERCATO_POSITION_QUOTA` | 2/6/6/4 | any other breakdown |
| `RANDOM_BID_STRATEGY` | `deficit_weighted` | another generator |

Two of these are worth a word.

**The Valise and own goals.** Spec 3.6 gives a numeric formula that counts every real
goal in the Valise's pool; the bonus table of spec 3.5 says a Valise never cancels an own
goal. They disagree, and the disagreement changes the score when a side's only goals are
one ordinary and one own goal. The default follows the normative formula, and
`valise_can_cancel_real_own_goal=False` follows the table. The own goal conceded through
phantom players is outside the pool under either reading — that one is an invariant, not
a flag.

**The quota.** "At least 18 players" is all the official page says. 2 goalkeepers, 6
defenders, 6 midfielders and 4 forwards is the smallest squad that can field 11 starters
and 7 substitutes including a goalkeeper, which is why it is the default.

## Data

Only the unauthenticated base is used, with an identifiable `User-Agent` and an hourly
cadence (fifteen minutes on a match day).

```
GET /api/data/championships/active
GET /api/data/championship-clubs
GET /api/data/championship-players-pool/{championshipId}[?season=YYYY]
GET /api/data/championship-player-stats-summary/{playerId}
```

Ingestion has three obligations:

- **quotations are historised, never overwritten** — the mercato and the market need the
  series, and a new row is only appended when the value actually moves;
- **ratings are upserted on `(player_id, match_id)`**, because a rating can change after
  a postponed match;
- **a rating that changes under an already-resolved fixture flags it for recompute**
  rather than quietly rewriting a published result. `mpg.cli replay <fixture_id>` does
  the recompute, explicitly.

`tests/fixtures/data/` holds trimmed real payloads from 21 September 2026, so the
end-to-end test runs the whole pipeline — ingestion, mercato, lineups, resolution,
standings — with no network access.

## Commands

```bash
python -m mpg.cli ingest [--championship 1] [--seasons 2024,2025,2026]
python -m mpg.cli resolve-round <round_id>
python -m mpg.cli resolve-week <league_id> <game_week>
python -m mpg.cli replay <fixture_id>
python -m mpg.cli standings <league_id>
python -m mpg.cli scenarios
```

## Out of scope

Expert mode, play-offs beyond the tie-break rule, multi-divisions, badges, chat, and any
front end.

## Legal

The rules of a game are not protectable as such. The names are: "Mon Petit Gazon" and
"MPG", the phantom player, and the bonus names. This repository uses them as a reading
of the specification it was built from, which is fine for a personal or portfolio
project. **Before publishing anything, rename the game, the bonuses and the phantom
player.** The public API is called at a reasonable cadence with an identifiable
`User-Agent`.
