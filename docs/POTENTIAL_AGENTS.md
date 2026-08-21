# Potential Agents & Workers — Brainstorm

Backlog of ideas for future sub-agents and system-supporting workers to add to the
Jarvis multi-agent network. Two layers: **horizontal helpers** that improve the
whole system, and **purpose-specific ("celowe") agents** for daily use.

> Convention reminders (see `CLAUDE.md`): user-facing replies in Polish, code/instructions
> in English; agents registered via `@agents_decorator`, tools via `@tool_ownership` +
> `@function_tool`, models via `@llm_usage`. `get_current_date_and_time` is the model
> for a "context enrichment" helper.

---

## 1. Horizontal / system-supporting agents & workers

These often give more leverage than another vertical agent. `get_current_date_and_time`
is an example of the pattern (context enrichment).

### a) Context / Environment provider (extend what exists)
Light agent (or a set of cheap tools available to the coordinator) that supplies
"world state" for better interpretation of requests:
- `get_current_date_and_time` ✅ (already have)
- `get_user_location` / `resolve_timezone` — default Warsaw, useful for "from here" weather/route
- `get_base_currency_and_locale` — PLN, pl-PL (finance & news can use it)
- `resolve_entity` — map "mama", "dom", "praca", "mój samochód" → concrete contact/address/device
  so vertical agents don't have to guess.

**Why:** these values repeatedly feed back as parameters to other tools — exactly like date/time.

### b) Planner / Decomposer
For complex requests ("zaplanuj mi jutro rano wyjście"), the coordinator can be a weak
planner. A dedicated planner decomposes into steps and decides which agents to fire
(and whether in parallel). Naturally an "agent-as-tool" called by the coordinator.

### c) Verifier / Critic (reflection)
Guard agent that checks output consistency **before** returning (e.g. finance didn't mix
up currency, route actually answers the question). The "evaluator/reflection" pattern
raises quality. Can be enabled only for irreversible/costly actions.

### d) Response Composer (formatter)
Fits the "**Polish replies, English code/instructions**" convention. Instead of scattering
Polish formatting across all agents, one composer takes raw data from sub-agents and
assembles a coherent Polish reply in a fixed style (units, number format, PLN). Unifies
tone — important once messages go to the phone.

### e) Notification / Delivery worker
**Key for the phone vision.** A separate "output transport" — sending messages
(Messenger/iMessage/push). The other side of the seam already planned for the phone
front-end: "handle one message → return one reply" + `deliver(message, channel)`.
Without it, proactive briefs have no way out.

### f) Scheduler agent
Roadmap item 3. Tools: `create_job`, `list_jobs`, `edit_job`, `delete_job` (cron-like).
The "alternate entry path" into the system. `memory_operator` should be able to edit it
(e.g. set a reminder).

### g) Resilience / retry helper
Thin layer: error normalization, retry with backoff, fallback ("Yahoo down → try another
source"). In practice less an "agent", more a shared decorator over tools — but worth
designing deliberately.

> Note: **c/d are often realized as guardrails/a layer rather than a full agent** —
> a design decision, but conceptually distinct roles.

---

## 2. Purpose-specific ("celowe") daily-use agents

### `fpl_agent` — Fantasy Premier League (first candidate)
Public, **keyless** API (`fantasy.premierleague.com/api/…` returns JSON) — ideal for the
`@tool_ownership` pattern.

Sketch of tools:
- `get_my_team(entry_id)` — squad, bench, captain, budget
- `get_gameweek_status()` — deadline (pairs great with `get_current_date_and_time` → "deadline in 3h" reminder)
- `get_player(name)` — form, xG/xA, minutes, cards, price, ownership %
- `get_fixtures(team, next_n)` — fixture difficulty (FDR)
- `suggest_transfers()` / `suggest_captain()` — analysis for the upcoming gameweek
- `get_price_changes()` / `get_injury_news()` — the latter pairs with `news_agent`

Ties naturally to the scheduler ("Friday morning, remind me and suggest captain") and
delivery — so it exercises the whole target architecture end-to-end.

### Other daily candidates (sorted by value / integration cost)

| Agent | Tools / API | Auth | Notes |
|---|---|---|---|
| **calendar_agent** | Google Calendar: today/tomorrow, free slots, create event | OAuth | Huge daily value; sending invites = action needs consent |
| **transit_agent** | ZTM Warsaw / Jakdojade: public-transport routing | key/scrape | Very "Warsaw", complements `maps_agent` (car vs. tram) |
| **parcel_agent** | InPost / parcel tracking | token | Polish context, daily |
| **air_quality_agent** | GIOŚ / smog + pollen | keyless-ish | Warsaw in winter — genuinely useful, cheap win |
| **email_agent** | Gmail: summarize, search, draft | OAuth | Draft OK; sending needs user consent |
| **tasks_agent** | TODO list / reminders | JSON/local | Pairs well with memory + scheduler |
| **spotify_agent** | playback/playlists | OAuth | "Fun", easy demo |
| **health_agent** | steps/sleep (Apple Health export) | local | Good for the morning brief |
| **energy_agent** | electricity prices / dynamic tariffs (RCE) | keyless | "When to run the washer" — pairs with `iot_operator` |
| **crypto/portfolio** | extend finance | keyless | Low-hanging fruit on top of `finance_agent` |

**Security/arch note:** OAuth agents (Calendar, Gmail, Spotify) and any **sending /
irreversible actions** are the natural place for the Verifier/confirmation gateway from
the horizontal layer — one consent mechanism, many consumers.

### More vertical ideas (grouped)

FPL was just the seed example — a broader backlog, tailored to the owner (Warsaw, daily use).

**Food & home**
- **`meal_agent`** — recipes from what's in the fridge, weekly meal plan → generates a shopping
  list (pairs with `tasks_agent` + scheduler). API: Spoonacular / TheMealDB (keyless).
- **`groceries_agent`** — online groceries: Frisco/Barbora — cart, delivery slots. Very "Warsaw", daily. Auth: store account.
- **`plant_care_agent`** — watering/fertilizing schedule, reminders. Nice small scheduler + delivery example.

**Money & shopping** (distinct from market-focused `finance_agent`)
- **`budget_agent`** — **personal** expenses: log "coffee 15 zł", monthly summaries, categories,
  budget. Totally different use-case from FX/stocks. Storage: JSON under `data/`.
- **`price_watch_agent`** — price alerts: Ceneo/Allegro — "ping me when PS5 drops below 2000 zł". Scheduler + delivery in action.
- **`car_agent`** — nearby fuel prices, reminders for inspection/insurance/oil change, where to fill up cheap.

**Culture & leisure**
- **`watch_agent`** — what to watch: where a film/series is available (JustWatch), ratings, "add to watchlist".
- **`events_agent`** — Warsaw concerts/events, ticket availability (Going/eBilet). Pairs with `calendar_agent`.
- **`sports_agent`** — general football scores/standings (and other sports) — broader than FPL. API: football-data.org (keyless tier).
- **`reading_agent`** — books/podcasts: recommendations, to-read queue, "how many pages today".

**Personal / social**
- **`nameday_agent`** — Polish **imieniny**: whose name day is today, reminder to send wishes.
  Very Polish, keyless (static table), cheap and pleasant.
- **`fitness_agent`** — running/activity (Strava): recent workouts, weekly mileage, goal. Good for the morning brief. Auth: OAuth.
- **`language_agent`** — word of the day / spaced repetition (if learning a language), short exercises.
- **`knowledge_agent`** — "second brain": Obsidian/Notion notes — save a thought, search, summarize.
  Works great with `memory_operator`.

**Practical**
- **`travel_agent`** — flights/hotels: deals, connection search, price alerts on a route.

> Good **first** verticals (keyless + daily): `nameday_agent`, `sports_agent`, `meal_agent`,
> `budget_agent` (purely local). Leave OAuth ones (Strava, Notion, stores) for later, alongside
> the consent gateway from the horizontal layer.

---

## Recommended order

1. **Notification/Delivery + Scheduler** (horizontal) — unlock the whole "proactive briefs
   on the phone" vision; prerequisite for vertical agents to be worthwhile.
2. **`fpl_agent`** — great first vertical: keyless API, immediately used weekly, and it
   exercises scheduler + delivery + context(date/time) end-to-end.
3. **Response Composer** — once messages go to the phone, consistent Polish formatting matters.
4. Then `calendar_agent` / `air_quality` / `transit` for the morning brief.
