"""Seed scripted conversations — the deterministic multi-turn benchmark set.

Each conversation is a fixed sequence of user turns (owner persona, Polish). Turns may carry
``expected_routing`` for per-turn routing metrics. Conversations exercise things a single-turn
eval cannot: cross-turn context ("a ile kosztuje funt?" after naming a trip) and knowledge
retention (recall a fact the user stated several turns earlier).
"""

from __future__ import annotations

from evals.harness.conversation import ScriptedConversation, ScriptedTurn as T


# Scenarios for the DYNAMIC (simulated) layer: (scenario, expected_outcome) pairs the owner
# persona pursues. Fed to `simulator.make_conversational_golden` — no fixed turns, the
# simulated user LLM improvises them.
SIM_SCENARIO_SPECS: list[tuple[str, str]] = [
    (
        "Właściciel planuje jutrzejszy poranny wyjazd na lotnisko Chopina i chce znać pogodę "
        "oraz czas dojazdu, a na końcu ustawić przypomnienie o wyjściu.",
        "Użytkownik zna pogodę i czas dojazdu i ma ustawione przypomnienie.",
    ),
    (
        "Właściciel prosi o poranny brief i dopytuje o szczegóły (kurs, pogoda, newsy), "
        "zmieniając zdanie w trakcie rozmowy.",
        "Użytkownik dostał brief i odpowiedzi na dopytania, spójne z wcześniejszymi turami.",
    ),
    (
        "Właściciel ustala preferencje oświetlenia na wieczór i chce, by asystent zapamiętał je "
        "na przyszłość.",
        "Preferencja została zapamiętana, a asystent potwierdził ustawienie światła.",
    ),
]


SEED_CONVERSATIONS: list[ScriptedConversation] = [
    # 1) Trip planning — context carries the destination across turns; multiple agents.
    ScriptedConversation(
        id="trip_london",
        scenario="Właściciel planuje jutrzejszy wyjazd do Londynu i dopytuje o szczegóły.",
        turns=[
            T("Wybieram się jutro do Londynu."),
            T("Jaka będzie tam pogoda?", expected_routing=["weather_agent"]),
            T("A ile kosztuje funt?", expected_routing=["finance_agent"]),
            T("Ustaw mi przypomnienie, żebym spakował parasol.", expected_routing=["scheduler_agent"]),
        ],
    ),
    # 2) Knowledge retention — the assistant must recall a fact stated 2 turns earlier,
    #    across a distractor turn (weather). Tests conversation continuity, not the store.
    ScriptedConversation(
        id="coffee_memory",
        scenario="Właściciel podaje preferencję, potem pyta o coś innego, na końcu prosi o przypomnienie preferencji.",
        turns=[
            T("Zapamiętaj, że nie piję kawy z mlekiem — zawsze czarna.",
              expected_routing=["memory_operator"]),
            T("Jaka jest teraz pogoda w Warszawie?", expected_routing=["weather_agent"]),
            T("Przypomnij mi, jak piję kawę?"),
        ],
    ),
    # 3) Brief with follow-up — the follow-up refers back to the brief's content.
    ScriptedConversation(
        id="brief_followup",
        scenario="Właściciel prosi o poranny brief, a potem dopytuje o szczegół z briefu.",
        turns=[
            T("Daj mi poranny brief: pogoda w Warszawie i kurs euro.",
              expected_routing=["weather_agent", "finance_agent"]),
            T("A jaka była ta temperatura?"),
        ],
    ),
]
