"""The agents: a deterministic baseline, and one driven by a model.

The baseline is not a straw man. It bids on the best-quoted players of the positions
it is short of, and it fields its best-rated eleven. A model that cannot beat it is
not adding anything, which is exactly what the bench is for.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from mpg.bench.ollama import Completion
from mpg.bench.prompts import MAX_BIDS_PER_ROUND, lineup_prompt, mercato_prompt
from mpg.bench.types import (
    AgentCall,
    BidDecision,
    LineupAnswer,
    LineupView,
    MercatoAnswer,
    MercatoView,
    PlayerCard,
)
from mpg.bench.validation import clamp_bids, repair_lineup
from mpg.engine.lines import Line


class Agent(Protocol):
    name: str

    def bid(self, view: MercatoView) -> tuple[list[BidDecision], AgentCall]: ...

    def pick(self, view: LineupView) -> tuple[LineupAnswer, AgentCall]: ...


# ------------------------------------------------------------------------- baseline


@dataclass(slots=True)
class HeuristicAgent:
    """No model: the control arm.

    Spends an even share of its budget each round on the best-quoted players of the
    positions it lacks, and fields the best-rated legal eleven it can.
    """

    name: str = "heuristique"
    seed: int = 42
    #: How far above the quotation it bids, to survive a tie.
    premium: float = 0.15

    def bid(self, view: MercatoView) -> tuple[list[BidDecision], AgentCall]:
        envelope = view.budget // max(1, view.rounds_remaining)
        targets = max(1, min(MAX_BIDS_PER_ROUND, -(-view.missing // 3)))

        deficit = {line: count for line, count in view.deficit.items() if count > 0}
        chosen: list[BidDecision] = []
        spent = 0
        taken: set[str] = set()

        for _ in range(targets):
            if not deficit:
                break
            line = max(sorted(deficit), key=lambda ln: deficit[ln])
            candidates = [
                card for card in view.free_players
                if card.line is line and card.player_id not in taken
            ]
            # Prefer a player who actually turns out, at equal quotation.
            candidates.sort(
                key=lambda c: (
                    -round(c.availability if c.availability is not None else 0.0, 1),
                    -c.quotation,
                    c.player_id,
                )
            )
            reserve = max(0, view.missing - len(chosen) - 1)
            ceiling = min(envelope - spent, view.budget - spent - reserve)
            affordable = [c for c in candidates if c.quotation <= ceiling]
            if not affordable:
                deficit.pop(line, None)
                continue
            card = affordable[0]
            amount = min(ceiling, max(card.quotation, round(card.quotation * (1 + self.premium))))
            chosen.append(BidDecision(card.player_id, int(amount)))
            taken.add(card.player_id)
            spent += amount
            deficit[line] -= 1
            if deficit[line] <= 0:
                deficit.pop(line)
        return chosen, AgentCall("mercato", self.name)

    def pick(self, view: LineupView) -> tuple[LineupAnswer, AgentCall]:
        answer = LineupAnswer(formation="4-4-2", note="meilleures notes moyennes")
        by_line = view.by_line()

        def rank(card: PlayerCard) -> tuple:
            # Availability first: a starter who does not play costs more than a
            # mediocre one who does.
            return (
                -(card.availability if card.availability is not None else 0.0),
                -(card.average_rating or 0),
                -card.quotation,
                card.player_id,
            )

        def best(line: Line, count: int, skip: set[str]) -> list[str]:
            cards = [c for c in by_line.get(line, []) if c.player_id not in skip]
            cards.sort(key=rank)
            return [c.player_id for c in cards[:count]]

        used: set[str] = set()
        starters: list[str] = []
        for line, count in ((Line.G, 1), (Line.D, 4), (Line.M, 4), (Line.A, 2)):
            picks = best(line, count, used)
            starters.extend(picks)
            used.update(picks)
        answer.starters = starters

        bench = best(Line.G, 1, used)
        used.update(bench)
        for line, count in ((Line.D, 2), (Line.M, 2), (Line.A, 2)):
            picks = best(line, count, used)
            bench.extend(picks)
            used.update(picks)
        answer.bench = bench

        # The captain is the best-rated outfield starter: the goalkeeper is excluded
        # by the rules, and the bonus is wasted on a player who is unlikely to shine.
        rated = {card.player_id: card for card in view.squad}
        outfield = [pid for pid in starters if rated[pid].line is not Line.G]
        outfield.sort(
            key=lambda pid: (
                -(rated[pid].availability if rated[pid].availability is not None else 0.0),
                -(rated[pid].average_rating or 0),
                pid,
            )
        )
        answer.captain = outfield[0] if outfield else None
        return answer, AgentCall("lineup", self.name)


# ------------------------------------------------------------------- model-driven


def extract_json(text: str) -> dict | None:
    """Pull an object out of whatever the model said.

    Small models wrap JSON in prose, in fences, or emit it twice. This takes the first
    balanced object it can parse and gives up rather than guessing.
    """
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    depth = 0
    start = None
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    value = json.loads(text[start:index + 1])
                except json.JSONDecodeError:
                    start = None
                    continue
                if isinstance(value, dict):
                    return value
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+", value)
        if match:
            return int(match.group(0))
    return None


def _as_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            for key in ("player_id", "id", "joueur"):
                if isinstance(item.get(key), str):
                    out.append(item[key])
                    break
    return out


@dataclass(slots=True)
class LlmAgent:
    """An agent driven by a completion function.

    Whatever the model answers is validated and repaired before it reaches the game,
    and a model that answers nothing usable falls back on the baseline rather than
    stalling a run. Both facts are recorded: a bench that only measured points would
    hide the difference between a bad manager and a broken one.
    """

    name: str
    complete: object                       # Callable[[str], Completion]
    fallback: HeuristicAgent = field(default_factory=HeuristicAgent)

    def bid(self, view: MercatoView) -> tuple[list[BidDecision], AgentCall]:
        completion: Completion = self.complete(mercato_prompt(view))  # type: ignore[operator]
        call = AgentCall("mercato", self.name, completion.seconds, completion.tokens,
                         raw=completion.text[:2000])
        if completion.error:
            call.failure = completion.error
            return self.fallback.bid(view)[0], call

        payload = extract_json(completion.text)
        if payload is None:
            call.failure = "unparseable"
            return self.fallback.bid(view)[0], call

        raw_bids = payload.get("bids") or payload.get("encheres") or []
        answer = MercatoAnswer(note=str(payload.get("note", ""))[:300])
        if isinstance(raw_bids, list):
            for item in raw_bids:
                if not isinstance(item, dict):
                    continue
                player_id = item.get("player_id") or item.get("id") or item.get("joueur")
                amount = _as_int(item.get("amount", item.get("montant")))
                if isinstance(player_id, str) and amount is not None:
                    answer.bids.append(BidDecision(player_id, amount))

        kept, repair = clamp_bids(answer, view)
        if repair.problems:
            call.failure = "; ".join(repair.problems[:4])
        if not kept:
            # Nothing usable at all: the baseline keeps the run going.
            call.failure = (call.failure or "no usable bid") + " | fallback"
            return self.fallback.bid(view)[0], call
        return kept, call

    def pick(self, view: LineupView) -> tuple[LineupAnswer, AgentCall]:
        completion: Completion = self.complete(lineup_prompt(view))  # type: ignore[operator]
        call = AgentCall("lineup", self.name, completion.seconds, completion.tokens,
                         raw=completion.text[:2000])
        if completion.error:
            call.failure = completion.error
            return self.fallback.pick(view)[0], call

        payload = extract_json(completion.text)
        if payload is None:
            call.failure = "unparseable"
            return self.fallback.pick(view)[0], call

        captain = payload.get("captain") or payload.get("capitaine")
        answer = LineupAnswer(
            formation=str(payload.get("formation", "4-4-2")),
            starters=_as_ids(payload.get("starters") or payload.get("titulaires")),
            bench=_as_ids(payload.get("bench") or payload.get("remplacants")),
            captain=captain if isinstance(captain, str) else None,
            note=str(payload.get("note", ""))[:300],
        )
        fixed, repair = repair_lineup(answer, view)
        if repair.problems:
            call.failure = "; ".join(repair.problems[:4])
        return fixed, call
