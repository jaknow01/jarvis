## Context

`watch-execution` definiuje kontrakt `{code, data} -> bool` i wstrzykuje w runner **stub**
ewaluatora (niezabezpieczony, dev-only). Ten change dostarcza produkcyjną, izolowaną
implementację tego samego kontraktu. Jarvis i sandbox biegną na **tym samym urządzeniu**
(Raspberry Pi), więc kanałem jest **unix socket**, a `--network=none` nie utrudnia
komunikacji. Threat model: kod predykatu jest generowany przez LLM (change `watch-authoring`),
więc traktujemy go jak **nieufny** — bronimy się przed (a) runaway-code i (b) ewentualną próbą
eksfiltracji/nadużycia. Na ARM twarda granica jądra (gVisor/microVM) jest niepraktyczna, więc
granicą jest **namespace (rootless Podman) + hermetyczny Starlark** — dwie warstwy szeregowe.

## Goals / Non-Goals

**Goals**
- Izolowany warm-serwis implementujący kontrakt w dwóch trybach (runtime `bool|unknown`,
  authoring `validate/evaluate` z treścią błędu).
- Starlark jako granica strukturalna: `def check(data)` + wstrzykiwane helpery.
- Wymagane właściwości izolacji (brak sieci, non-root, read-only, limity) — z konkretną
  konfiguracją trzymaną **prywatnie** w `sandbox/`.
- Publiczny, cienki klient kontraktu + protokół socketu; „sandbox down → `unknown`".

**Non-Goals**
- Logika watchy / runner / rejestr źródeł (`watch-execution`).
- Generowanie / normalizacja / naprawa kodu (`watch-authoring`).
- gVisor / microVM (na ARM niepraktyczne).
- Wystawianie sandboxa przez sieć / multi-host (współdzielimy urządzenie).

## Decisions

### D1: Starlark przez `starlark-pyo3` (in-process), `starlark-python` jako fallback
Spike potwierdził API: `parse` → `Module()` z `mod["data"]=…` (wstrzyknięcie danych) →
`add_callable(name, fn)` (helpery) → `eval` → `freeze().call("check", data)` → `bool` (przez
JSON). To wprost realizuje konwencję `def check(data)`.
- *ARM:* Rust/maturin — kompilacja dzieje się **raz przy build-image** (nie runtime), ew. przez
  buildx; Pi tylko uruchamia obraz.
- *Fallback:* `starlark-python` (czysty Python, zero natywnego builda) — workload trywialny, więc
  wolniejszość bez znaczenia; ryzyko = dojrzałość portu.
- *Alternatywy odrzucone:* pełny CPython (nie jest granicą; tylko filtr), własny DSL (modele piszą
  Starlark/Python dużo pewniej niż autorską składnię).

### D2: Kontrakt ma dwie twarze (jeden ewaluator, dwie odpowiedzi)
Runtime chce `bool|unknown` i **odrzuca** treść błędu; authoring chce **treści błędu** (paliwo dla
repair-LLM w `watch-authoring`). Więc serwis wystawia `validate(code)` i `evaluate(code, data)` z
błędem, a runner używa wąskiego widoku `bool|unknown`.
- *Alternatywa:* tylko `-> bool`. Odrzucona — authoring nie miałby jak naprawiać kodu.

### D3: Granica publiczne vs prywatne = kontrakt vs metoda zabezpieczenia
Sekretem jest **sposób** zabezpieczenia (flagi kontenera, sieć, seccomp, caps, warstwy, Quadlet),
nie sam fakt istnienia sandboxa ani kontrakt. Publiczne repo: klient + protokół. `sandbox/`
(git-ignored): serwis + Containerfile + hardening + Quadlet + prywatny `DESIGN`. Specy OpenSpec
opisują **właściwości** izolacji (cel), nie **konfigurację** (metodę).
- *Alternatywa:* wszystko publiczne. Odrzucona — świadomie rezygnujemy z „security by obscurity"
  jako *jedynej* obrony, ale traktujemy nieujawnianie metody jako **dodatkową** warstwę.

### D4: Warm serwis + unix socket + świeże środowisko per-wywołanie
Długożyjący kontener (cold start Podmana >> czas evala, więc nie spawnujemy per-poll). Socket na
współdzielonym, zapisywalnym mount przy read-only rootfs. Na każde wywołanie **świeży `Module()`**
(Starlark hermetyczny → brak wycieku stanu), limit kroków starlark-rust + gruby wall-clock guard.
- *Alternatywa:* kontener per-poll. Odrzucona — marnotrawstwo, cold start.

### D5: Izolacja per-wywołanie ≠ rozluźnienie warstw
Warm serwis obsługuje wiele predykatów w czasie → ryzyko wycieku stanu i „jeden zły predykat kładzie
serwis dla wszystkich". Mitigacja w D4 (świeże środowisko + limity). Zasada nadrzędna: **hartuj
kontener i Starlark tak, jakby drugiej warstwy nie było.**

### D6: Nadzór przez Podman Quadlet
Plik `.container` → systemd (user) generuje unit: auto-restart, start przy bootcie, rootless. Unit
prywatny (`sandbox/`).
- *Alternatywa:* ręczne `podman run` / skrypt. Odrzucona — brak nadzoru/restartu.

## Risks / Trade-offs

- **`starlark-pyo3` nie zbuduje się na arm64** → Mitigacja: kompilacja przy build-image (buildx),
  a jak boli — fallback `starlark-python` (czysty Python).
- **Namespace to nie VM** (ARM) → Mitigacja: druga warstwa (hermetyczny Starlark, który sam nie
  robi syscalli) + pełny hardening kontenera; świadomie akceptowany pułap.
- **Rozjazd kontraktu ze stubem/authoringiem** → Mitigacja: jeden „shared" opis kontraktu
  (konwencja `def check`, helpery, `unknown`/`error`); testy kontraktu po obu stronach.
- **Helpery wstrzykiwane przez `add_callable` to kod Pythona** → Mitigacja: helpery są **nasze**,
  minimalne i czyste; nie przyjmują kodu od predykatu.
- **Serwis padł / socket głuchy** → `unknown` (nie `false`); watch skip+retry. Zapisane jako wymaganie.
- **Latencja socketu w pętli** → mała (lokalny socket, warm serwis); błąd/timeout → `unknown`.

## Migration Plan

- Produkcyjnie: podmiana punktu wstrzyknięcia ewaluatora w runnerze ze stubu na klienta socketu
  (feature-gate/konfig). Brak stubu = powrót do zachowania z `watch-execution`.
- Obraz budowany raz; wdrożenie = uruchomienie unitu Quadlet. Rollback = zatrzymanie unitu +
  przełączenie runnera z powrotem na stub (watche wtedy dają `unknown`).

## Open Questions

- **Protokół socketu:** JSON po linii / framed / mini-HTTP? (musi nieść `mode`, `code`, `data`,
  oraz `result | error`). Rozstrzygnięcie przy implementacji klienta+serwisu.
- **Zestaw helperów v1:** tylko `get(data, path, default)`, czy od razu helpery czasowe? (Starlark
  nie ma `datetime`; jeśli warunki będą czasowe — trzeba wstrzyknąć.)
- **Limity Starlarka:** konkretne wartości step/time; czy `starlark-pyo3` wystawia przerwanie po
  wall-clock, czy polegamy na limicie kroków + guard w serwisie.
- **`starlark-pyo3` vs `starlark-python` — decyzja twarda** po próbie builda arm64 (spike wykonawczy
  przy implementacji `sandbox/`).
