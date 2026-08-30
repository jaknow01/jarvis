## Context

Predykat watcha jest kodem pisanym przez LLM, a Starlark to okrojony Python — model łatwo
generuje niedozwolone konstrukcje (`try/except`, f-string, `import`). `watch-execution`
zapisuje kod verbatim; to za mało do produkcji. Ten change dokłada **authoring-time pipeline**,
który zamienia intencję w **zwalidowany, potwierdzony** predykat. Fundamentalna zasada:
**rozdziel arbitralność od determinizmu w czasie** — LLM działa raz (przy tworzeniu), a runtime
wykonuje gotowy kod deterministycznie, bez LLM.

## Goals / Non-Goals

**Goals**
- Niezawodnie: intencja → poprawny Starlark (`def check(data)`), mimo Python-izmów modelu.
- Walidacja „prawdziwa" (przez sandbox), iteracyjna naprawa z capem i fail-closed.
- Semantyka, nie tylko składnia: smoke-test na realnej próbce + potwierdzenie usera.
- Zero kosztu w runtime; cały pipeline authoring-time.

**Non-Goals**
- Sandbox/Starlark (`watch-sandbox`); logika runtime watchy (`watch-execution`).
- Uczenie się promptu generatora w czasie; predykaty wielojęzyczne; auto-arm bez potwierdzenia.

## Decisions

### D1: Kolejność deterministyczna przed LLM
`generate → normalize (bez LLM) → validate (sandbox) → [fail] → repair-LLM → validate → …`.
Tanie, deterministyczne bramki (normalizer, walidacja) idą pierwsze; repair-LLM rezerwujemy na
realne problemy, nie na f-string.
- *Alternatywa:* od razu repair-LLM. Odrzucona — droższe/wolniejsze, mniej przewidywalne.

### D2: Walidacja/naprawa „na ślepo" przez sandbox (brak lokalnego Starlarka)
Repair-LLM nie ma własnego interpretera — dostaje **treść błędu z sandboxa** + reguły + intencję i
poprawia. Faktyczna kompilacja zawsze w sandboxie (jedno źródło prawdy o tym, co się kompiluje).
- *Konsekwencja:* tworzenie watcha wymaga **żywego sandboxa** → `watch-authoring` wdraża się po
  `watch-sandbox`.
- *Alternatywa:* własny `starlark` w authoringu do walidacji. Odrzucona (decyzja użytkownika) —
  duplikuje zależność i rozjeżdża „co się kompiluje".

### D3: Iteracja z twardym capem + fail-closed
„Aż będzie dobry" — tak, ale **z limitem** (np. 3–4 rundy). Po przekroczeniu: **nie uzbrajaj** watcha,
poproś usera o doprecyzowanie. Chroni przed nieskończoną pętlą i paleniem tokenów na patologii.
- *Alternatywa:* pętla bez limitu. Odrzucona — ryzyko zapętlenia/kosztu.

### D4: Generator = `scheduler_agent`; repair = osobny „kodowy" model
Generator to istniejący `scheduler_agent` (ma `create_watch`). Repair przez
`LLM_BY_AGENT["watch_coder"]` / `AGENT_WATCH_CODER_MODEL` — osobny, mocniejszy w kodzie/ tańszy;
zero nowej instalacji (jak `history_compressor`). Wartość rozdziału realna tylko przy różnicy
zdolność/koszt; inaczej repair to po prostu kolejna tura tego samego modelu.
- *Alternatywa:* jeden model do wszystkiego. Dozwolona (konfiguracyjnie) — architektura na to pozwala.

### D5: Walidacja ≠ poprawność → smoke-test + potwierdzenie
Kompilacja nie dowodzi, że predykat robi, co trzeba (może zawsze zwracać `false` / czytać zły klucz).
Smoke-test: pobierz raz przez rejestr źródeł, `evaluate(code, sample)` w sandboxie, pokaż wynik i
**potwierdź** przed uzbrojeniem. To realna sieć na semantykę, nie repair-LLM.
- *Alternatywa:* ufać walidacji składni. Odrzucona — przepuszcza błędy logiczne / złe ścieżki pól.

### D6: Normalizer minimalny
Tylko najczęstsze Python-izmy, na których „normalny" LLM się wywala (f-string, ewentualnie kilka
innych mechanicznych). Nie semantyczny, nie ambitny — ma odciążyć repair-LLM z trywialnych rzeczy.

## Risks / Trade-offs

- **Tworzenie watcha zależne od żywego sandboxa** → jeśli sandbox down, `create_watch` nie zwaliduje/
  nie zrobi smoke-testu. Mitigacja: jasny komunikat do usera („sandbox niedostępny, spróbuj później"),
  nie zapisuj połowicznie.
- **Repair-LLM wprowadza z powrotem zakazane konstrukcje** → Mitigacja: repair dostaje **te same reguły**
  co generator + treść błędu; walidacja po każdej rundzie.
- **Predykat poprawny składniowo, zły semantycznie** → Mitigacja: smoke-test + potwierdzenie (D5).
- **Cap za niski/za wysoki** → za niski: fałszywe fail-closed; za wysoki: koszt. Mitigacja: rozsądny
  default (3–4), konfigurowalny.
- **Rozjazd konwencji z sandboxem/stubem** → Mitigacja: generator emituje dokładnie `def check(data)` +
  helpery z kontraktu; smoke-test wykonuje to w prawdziwym sandboxie (wychwyci rozjazd).

## Migration Plan

- `create_watch` zmienia zachowanie: verbatim → authored. Wdrożenie po `watch-sandbox` (validate/smoke
  wymagają sandboxa). Rollback: przywrócenie verbatim-owego `create_watch` z `watch-execution` (watch
  bez walidacji — tylko awaryjnie/dev).
- Feature-gate: dopóki `watch_coder`/sandbox nie skonfigurowane, `create_watch` może działać w trybie
  „tylko walidacja bez repair" lub odmówić — do ustalenia przy implementacji.

## Open Questions

- **Skąd „opis intencji" dla repair-LLM?** Z oryginalnej wiadomości usera, czy generator produkuje
  zwięzłe streszczenie intencji obok kodu? (Drugie jest pewniejsze dla repairu.)
- **Jak podać potwierdzenie userowi?** Zwykłą odpowiedzią (composer, po polsku: „teraz zwraca X — OK?"),
  czy dedykowanym krokiem? Spójność z obecnym przepływem tur.
- **Wartość capu** (3? 4?) i zachowanie przy fail-closed (jaka dokładnie prośba o doprecyzowanie).
- **Zakres normalizera v1** — które dokładnie Python-izmy (poza f-stringiem) trafiają do mechanicznej
  transformacji, a które zostawiamy repair-LLM.
- **Formalna delta na `conditional-watches`** (verbatim → authored) — pogodzić przy archiwizacji
  `watch-execution` (dziś opisane jako wymaganie w `watch-authoring`).
