"""Curated, labelled goldens: single-hop and multi-hop, calibrated to what the agents can do.

Hand-authored (not synthetic) so every input is answerable by the real agents and carries
ground-truth labels:

- ``expected_routing`` — the sub-agent(s) the coordinator SHOULD delegate to. Present on
  every golden; drives ``routing_correctness``.
- ``expected_execution`` — ``{subagent: [real tool names]}`` for the defining tools. Provided
  for single-hop goldens (clean, single-agent execution); omitted for multi-hop, where the
  per-agent tool set is fuzzier and would make the metric noisy.

Tool names are the real public tools of each agent (see each ``lib/agents/<name>/tools.py``):
  weather_agent: current_weather, weather_forecast, get_current_date_and_time
  finance_agent: get_exchange_rate, get_stock_quote
  maps_agent:    get_route_details, get_maps_memory
  news_agent:    search_news
  iot_operator:  get_devices_state, turn_on_devices, turn_off_devices,
                 change_lighting_mode, change_color, change_light_temperature
  memory_operator: get_memory, save_memory, update_memory, delete_memory
  fpl_agent:     get_fpl_fixtures, get_pl_teams, get_my_fpl_squad, get_my_fpl_leagues,
                 get_fpl_league_standings, get_fpl_live
  scheduler_agent: create_scheduled_job, list_scheduled_jobs, delete_scheduled_job,
                   update_scheduled_job

``hop`` metadata tags each golden "single" or "multi" so reports can slice by difficulty.
"""

from __future__ import annotations

from evals.harness.datasets import EvalGolden


def _g(gid, text, routing, execution=None, hop="single") -> EvalGolden:
    return EvalGolden(
        id=gid,
        input=text,
        expected_routing=routing,
        expected_execution=execution,
        metadata={"hop": hop},
    )


# ============================================================ SINGLE-HOP (one agent)
SINGLE_HOP: list[EvalGolden] = [
    # weather
    _g("sh_weather_now", "Jaka jest teraz pogoda w Warszawie?",
       ["weather_agent"], {"weather_agent": ["current_weather"]}),
    _g("sh_weather_tomorrow", "Jaka będzie pogoda jutro po południu w Warszawie?",
       ["weather_agent"], {"weather_agent": ["weather_forecast"]}),
    _g("sh_weather_rain", "Czy dziś wieczorem w Warszawie będzie padać?",
       ["weather_agent"], {"weather_agent": ["weather_forecast"]}),
    # finance
    _g("sh_fx_eur", "Ile teraz kosztuje euro w złotówkach?",
       ["finance_agent"], {"finance_agent": ["get_exchange_rate"]}),
    _g("sh_fx_usd", "Jaki jest kurs dolara do złotego?",
       ["finance_agent"], {"finance_agent": ["get_exchange_rate"]}),
    _g("sh_stock_apple", "Po ile są teraz akcje Apple?",
       ["finance_agent"], {"finance_agent": ["get_stock_quote"]}),
    _g("sh_index_wig20", "Jak stoi dziś WIG20?",
       ["finance_agent"], {"finance_agent": ["get_stock_quote"]}),
    # maps
    _g("sh_route_home_work", "Ile zajmie mi teraz dojazd z domu do pracy?",
       ["maps_agent"], {"maps_agent": ["get_route_details"]}),
    _g("sh_route_airport", "Jak najszybciej dojadę autem na lotnisko Chopina?",
       ["maps_agent"], {"maps_agent": ["get_route_details"]}),
    # news
    _g("sh_news_world", "Co ważnego dzieje się dziś na świecie? Streść w kilku zdaniach.",
       ["news_agent"], {"news_agent": ["search_news"]}),
    _g("sh_news_markets", "Jakie są najnowsze wiadomości z rynków finansowych?",
       ["news_agent"], {"news_agent": ["search_news"]}),
    # iot
    _g("sh_iot_on_living", "Włącz światło w salonie.",
       ["iot_operator"], {"iot_operator": ["turn_on_devices"]}),
    _g("sh_iot_off_bedroom", "Zgaś światło w sypialni.",
       ["iot_operator"], {"iot_operator": ["turn_off_devices"]}),
    _g("sh_iot_state", "Które światła są teraz włączone?",
       ["iot_operator"], {"iot_operator": ["get_devices_state"]}),
    _g("sh_iot_warm_dim", "Ustaw w salonie cieplejsze, przygaszone światło.",
       ["iot_operator"]),  # tool set fuzzy (temp/mode/color) -> routing only
    # memory
    _g("sh_mem_save_coffee", "Zapamiętaj, że rano piję kawę bez cukru.",
       ["memory_operator"], {"memory_operator": ["save_memory"]}),
    _g("sh_mem_get_coffee", "Co zapamiętałeś o moich preferencjach kawowych?",
       ["memory_operator"], {"memory_operator": ["get_memory"]}),
    # fpl
    _g("sh_fpl_fixtures", "Jakie są najbliższe mecze w Premier League?",
       ["fpl_agent"], {"fpl_agent": ["get_fpl_fixtures"]}),
    _g("sh_fpl_squad", "Jak stoi mój skład w Fantasy Premier League?",
       ["fpl_agent"], {"fpl_agent": ["get_my_fpl_squad"]}),
    # scheduler
    _g("sh_sched_umbrella", "Przypomnij mi jutro o 8 rano, żeby wziąć parasol.",
       ["scheduler_agent"], {"scheduler_agent": ["create_scheduled_job"]}),
]


# ============================================================ MULTI-HOP (2-3 agents)
MULTI_HOP: list[EvalGolden] = [
    _g("mh_commute_weather", "Jaka jest teraz pogoda w Warszawie i ile zajmie dojazd z domu do pracy?",
       ["weather_agent", "maps_agent"], hop="multi"),
    _g("mh_fx_weather", "Ile kosztuje dziś dolar i jaka jest pogoda w Warszawie?",
       ["finance_agent", "weather_agent"], hop="multi"),
    _g("mh_news_wig20", "Jakie są dziś newsy z rynków i jak stoi WIG20?",
       ["news_agent", "finance_agent"], hop="multi"),
    _g("mh_weather_fx_forecast", "Jaka będzie jutro pogoda w Warszawie i ile kosztuje euro?",
       ["weather_agent", "finance_agent"], hop="multi"),
    _g("mh_airport_trip", "Sprawdź pogodę na jutro rano i ile zajmie dojazd na lotnisko Chopina.",
       ["weather_agent", "maps_agent"], hop="multi"),
    _g("mh_run_pref_weather", "Biegam rano — czy jutro w Warszawie będzie dobra pogoda na bieganie?",
       ["memory_operator", "weather_agent"], hop="multi"),
    _g("mh_pref_stocks", "Pamiętasz moje ulubione spółki? Sprawdź ich dzisiejsze kursy.",
       ["memory_operator", "finance_agent"], hop="multi"),
    _g("mh_news_schedule_brief", "Streść mi dzisiejsze najważniejsze newsy i zaplanuj taki skrót codziennie o 7 rano.",
       ["news_agent", "scheduler_agent"], hop="multi"),
    _g("mh_fpl_news", "Jak stoi mój skład FPL i czy są jakieś newsy o kontuzjach w Premier League?",
       ["fpl_agent", "news_agent"], hop="multi"),
    _g("mh_commute_mode", "Przy dzisiejszej pogodzie lepiej jechać autem czy iść pieszo z domu do pracy?",
       ["weather_agent", "maps_agent"], hop="multi"),
    _g("mh_fx_news_why", "Dlaczego dolar dziś tak drożeje? Sprawdź kurs i powiązane newsy.",
       ["finance_agent", "news_agent"], hop="multi"),
    _g("mh_morning_brief", "Daj mi poranny brief: pogoda w Warszawie, kurs euro i najważniejsze newsy.",
       ["weather_agent", "finance_agent", "news_agent"], hop="multi"),
    _g("mh_airport_fx", "Ile zajmie dojazd na lotnisko i po ile jest dziś dolar?",
       ["maps_agent", "finance_agent"], hop="multi"),
    _g("mh_mem_sched_dentist", "Zapamiętaj, że mam dentystę we wtorek, i przypomnij mi dzień wcześniej.",
       ["memory_operator", "scheduler_agent"], hop="multi"),
    _g("mh_weather_news", "Jaka jest pogoda w Warszawie i co ciekawego dziś w wiadomościach?",
       ["weather_agent", "news_agent"], hop="multi"),
    _g("mh_fpl_deadline", "Sprawdź moich zawodników FPL i przypomnij mi o zmianach przed najbliższym deadline'em.",
       ["fpl_agent", "scheduler_agent"], hop="multi"),
    _g("mh_travel_london", "Wybieram się do Londynu — jaka tam pogoda i ile kosztuje funt?",
       ["weather_agent", "finance_agent"], hop="multi"),
    _g("mh_news_league", "Jakie są najnowsze newsy o Premier League i jak wygląda tabela mojej ligi FPL?",
       ["news_agent", "fpl_agent"], hop="multi"),
    _g("mh_usual_commute_umbrella", "Jak zwykle jadę do pracy — sprawdź dojazd i czy powinienem wziąć parasol.",
       ["maps_agent", "weather_agent"], hop="multi"),
    _g("mh_light_dark_pref", "Lubię wieczorem ciepłe światło — jeśli na zewnątrz zrobiło się ciemno, ustaw je w salonie.",
       ["weather_agent", "iot_operator"], hop="multi"),
    _g("mh_evening_routine", "Sprawdź pogodę na jutro i przypomnij mi wieczorem, żeby przygotować ubranie.",
       ["weather_agent", "scheduler_agent"], hop="multi"),
    _g("mh_market_open_brief", "Poranny przegląd rynku: kurs euro i dolara, jak stoi WIG20 i najważniejsze newsy ekonomiczne.",
       ["finance_agent", "news_agent"], hop="multi"),
]


def all_curated() -> list[EvalGolden]:
    """Single-hop then multi-hop, in a stable order."""
    return SINGLE_HOP + MULTI_HOP
