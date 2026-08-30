## 1. Kontrakt authoringu ↔ sandbox

- [ ] 1.1 Uzgodnić z `watch-sandbox` tryb `validate(code)` i `evaluate(code, sample)` zwracające
      **treść błędu** (nie tylko `bool`); potwierdzić kształt odpowiedzi przez klienta kontraktu.
- [ ] 1.2 Ustalić wspólną konwencję (`def check(data)`) + listę helperów (`get`, ew. czasowe) jako
      jedno źródło prawdy dla generatora, repair-LLM i sandboxa.

## 2. Generator (scheduler_agent)

- [ ] 2.1 Prompt generatora: konwencja `def check(data)`, **zakazy** Starlarka (import/try-except/
      f-string/while/rekurencja/klasy), dostępne helpery, few-shot poprawnych predykatów.
- [ ] 2.2 Generator produkuje predykat **oraz** zwięzły „opis intencji" (do zasilenia repairu).
- [ ] 2.3 Wpięcie w przepływ `create_watch` (generacja przed zapisem, nie verbatim).

## 3. Normalizer (deterministyczny, bez LLM)

- [ ] 3.1 Minimalny normalizer najczęstszych Python-izmów (start: f-string → `.format()`); czysto
      mechaniczny, bez zmiany semantyki.
- [ ] 3.2 Uruchamiany przed walidacją; idempotentny; testowalny na parach wejście→wyjście.

## 4. Pętla walidacja ↔ repair

- [ ] 4.1 Walidacja: wołanie `predicate-sandbox.validate(code)`; sukces → dalej, porażka → treść błędu.
- [ ] 4.2 Repair-LLM przez `LLM_BY_AGENT["watch_coder"]` (`AGENT_WATCH_CODER_MODEL`): wejście = zepsuty
      kod + błąd sandboxa + opis intencji + reguły Starlarka; wyjście = poprawiony kod.
- [ ] 4.3 Iteracja z **twardym capem** (default 3–4) i **fail-closed**: po przekroczeniu — nie uzbrajaj,
      zwróć userowi prośbę o doprecyzowanie warunku.
- [ ] 4.4 Instrukcje dla repair-LLM (rozbudowane reguły Starlarka) jako osobny, utrzymywalny prompt.

## 5. Smoke-test + potwierdzenie

- [ ] 5.1 Pobranie próbki przez rejestr źródeł (`watch-sources` z `watch-execution`); `unknown` →
      zgłoś, nie uzbrajaj po cichu.
- [ ] 5.2 `evaluate(code, sample)` w sandboxie; przygotuj czytelny komunikat: co predykat teraz zwraca
      i na jakich danych.
- [ ] 5.3 Potwierdzenie użytkownika przed uzbrojeniem (spójnie z przepływem tur / composerem, po polsku);
      brak potwierdzenia → brak uzbrojenia.

## 6. Integracja `create_watch`

- [ ] 6.1 `lib/agents/scheduler_agent/tools.py`: `create_watch` routuje przez pipeline
      (generate → normalize → validate → repair(cap) → smoke → confirm) i zapisuje **dopiero** po sukcesie.
- [ ] 6.2 Zachowanie awaryjne, gdy sandbox/`watch_coder` niedostępne (odmowa z jasnym komunikatem lub
      tryb ograniczony) — ustalić i zaimplementować.
- [ ] 6.3 Zapis: gotowy, zwalidowany `check_code` trafia do store'u z `watch-execution`.

## 7. Testy

- [ ] 7.1 Normalizer: pary wejście→wyjście dla obsługiwanych Python-izmów; brak zmiany semantyki.
- [ ] 7.2 Pętla repair (fake-walidator + fake repair-LLM): zbieżność w N rund; fail-closed po capie
      (watch NIE uzbrojony).
- [ ] 7.3 Smoke-test: fake-fetch + fake-sandbox → poprawny komunikat wyniku; `unknown` → brak uzbrojenia.
- [ ] 7.4 `create_watch` end-to-end na fake'ach: happy-path (uzbrojony po potwierdzeniu) i fail-closed.
- [ ] 7.5 Konfiguracja `watch_coder`: repair używa `AGENT_WATCH_CODER_MODEL` niezależnie od generatora.

## 8. Dokumentacja

- [ ] 8.1 `docs/SCHEDULER.md` / `docs/` : opis pipeline'u authoringu (generate→repair→smoke), zależność od
      `predicate-sandbox`, cap + fail-closed, że runtime jest bez kosztu LLM.
- [ ] 8.2 `.env_template`: `AGENT_WATCH_CODER_MODEL` (+ ewentualny cap rund jako konfig).
