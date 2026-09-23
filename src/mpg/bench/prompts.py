"""The prompts. French, like everything a person reads.

Two lessons from measuring small models on this task shaped them. Asking for the whole
squad at once makes the answer long enough to be truncated, and a truncated answer is
never valid JSON -- so a round asks for a handful of bids. And the pool has to be
trimmed: a model given four hundred players spends its budget on the first ten.
"""

from __future__ import annotations

from mpg.bench.types import LineupView, MercatoView, PlayerCard
from mpg.engine.lines import Line
from mpg.presentation import LINE_LABEL

#: Bids asked for in one round. Short answers stay valid.
MAX_BIDS_PER_ROUND = 6

#: Free players shown per line. Enough to choose from, short enough to read.
POOL_PER_LINE = 14


def _card(card: PlayerCard) -> str:
    average = card.average_rating
    form = f", moyenne {average:.1f}".replace(".", ",") if average is not None else ""
    goals = f", {card.past_goals} but(s)" if card.past_goals else ""
    return (
        f"  {card.handle or card.player_id} | {card.name} | {LINE_LABEL[card.line]} "
        f"| cote {card.quotation}{form}{goals}"
    )


def _shortlist(view: MercatoView) -> list[PlayerCard]:
    """The best-quoted free players of each line that is still short."""
    out: list[PlayerCard] = []
    for line, missing in view.deficit.items():
        if missing <= 0:
            continue
        candidates = [card for card in view.free_players if card.line is line]
        candidates.sort(key=lambda c: (-c.quotation, c.player_id))
        out.extend(candidates[:POOL_PER_LINE])
    return out


def mercato_prompt(view: MercatoView) -> str:
    deficit = ", ".join(
        f"{count} {LINE_LABEL[line]}" for line, count in view.deficit.items() if count > 0
    ) or "aucun"
    squad = "\n".join(_card(card) for card in view.squad) or "  (effectif vide)"
    pool = "\n".join(_card(card) for card in _shortlist(view))
    handles = {card.player_id: card.handle for card in view.free_players}
    handles.update({card.player_id: card.handle for card in view.squad})
    already = "\n".join(
        f"  {handles.get(bid.player_id, bid.player_id)} pour {bid.amount} M€"
        for bid in view.own_bids
    ) or "  (aucune)"

    return f"""Tu diriges l'équipe « {view.team_name} » dans un jeu de fantasy football.

MERCATO — TOUR {view.round_number} (il reste environ {view.rounds_remaining} tours)

Les règles qui comptent :
- Les enchères sont FERMÉES : tu ne vois pas celles des autres, le plus offrant emporte le joueur.
- L'enchère minimale sur un joueur est sa cote.
- La somme de tes enchères de ce tour ne peut pas dépasser ton budget.
- Il te faut au total 2 gardiens, 6 défenseurs, 6 milieux et 4 attaquants.
- Un joueur non remporté ne coûte rien : une enchère perdue ne débite rien.
- Garde de quoi compléter ton effectif lors des tours suivants.

Ton budget : {view.budget} M€
Postes qu'il te manque : {deficit}
Déjà engagé sur ce tour : {view.committed} M€

Ton effectif actuel :
{squad}

Tes enchères déjà posées :
{already}

Joueurs libres (les mieux cotés des postes qui te manquent) :
{pool}

Choisis AU PLUS {MAX_BIDS_PER_ROUND} joueurs à cibler ce tour. Vise en priorité les postes
qui te manquent. Pour chacun, propose un montant supérieur ou égal à sa cote : miser
exactement la cote risque de perdre l'enchère, surenchérir gaspille du budget.

Réponds UNIQUEMENT avec cet objet JSON, sans aucun texte autour :
{{"bids": [{{"player_id": "A07", "amount": 12}}], "note": "ta stratégie en une phrase"}}

Utilise EXACTEMENT les identifiants courts de la liste (par exemple A07, D12)."""


def lineup_prompt(view: LineupView) -> str:
    lines = []
    for line in (Line.G, Line.D, Line.M, Line.A):
        cards = [card for card in view.squad if card.line is line]
        if not cards:
            continue
        cards.sort(key=lambda c: (-(c.average_rating or 0), -c.quotation))
        lines.append(f"{LINE_LABEL[line].upper()} :")
        lines.extend(_card(card) for card in cards)
    squad = "\n".join(lines)

    where = "à domicile" if view.at_home else "à l'extérieur"
    against = f" contre « {view.opponent_name} »" if view.opponent_name else ""
    formations = ", ".join(view.formations)

    return f"""Tu diriges l'équipe « {view.team_name} » dans un jeu de fantasy football.

COMPOSITION — JOURNÉE {view.game_week}, {where}{against}

Comment les points se marquent :
- Les buts réels de tes joueurs comptent.
- Un joueur de champ marque aussi un « but MPG » s'il traverse les lignes adverses :
  on compare sa note à la moyenne de chaque ligne adverse. Il faut au moins 5,0, et
  chaque duel gagné coûte 1 point puis 0,5. Un défenseur doit franchir quatre lignes,
  un attaquant seulement deux : les attaquants marquent plus facilement.
- À domicile, une égalité de duel tourne à ton avantage.
- Un gardien ne marque jamais de but MPG.
- Un titulaire qui n'a pas joué est remplacé par le banc ; s'il ne reste personne à son
  poste, un joueur fantôme noté 2,5 le remplace, ce qui est très coûteux.

Tu dois aligner 11 titulaires et 7 remplaçants, dont AU MOINS UN GARDIEN sur le banc.
Formations possibles : {formations}

Les notes ci-dessous sont celles des journées précédentes, pas celle à venir.

Ton effectif :
{squad}

Réponds UNIQUEMENT avec cet objet JSON, sans aucun texte autour :
{{"formation": "4-4-2",
  "starters": ["G01", "D01", "D02", "D03", "D04", "M01", "M02", "M03", "M04", "A01", "A02"],
  "bench": ["G02", "D05", "D06", "M05", "M06", "A03", "A04"],
  "captain": "A01",
  "note": "ton raisonnement en une phrase"}}

Utilise EXACTEMENT les identifiants courts de la liste (par exemple A07, D12).
Le capitaine doit être un titulaire qui n'est pas le gardien."""
