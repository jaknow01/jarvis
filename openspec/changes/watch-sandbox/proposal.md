## Why

Watche (`conditional-watches`, change `watch-execution`) potrzebują wykonywać **kod
predykatu wygenerowany przez LLM** na każdym pollu. Change `watch-execution` dostarcza
tylko **in-process stub** ewaluatora — niezabezpieczony, wyłącznie do dev/testów.
Do produkcji potrzebna jest **izolowana** implementacja tego samego kontraktu: taka, w
której nieufny kod nie ma dostępu do sieci, systemu plików ani hosta, a runaway-code nie
kładzie asystenta. Ten change buduje ten sandbox.

**Uwaga bezpieczeństwa:** dokładna metoda zabezpieczenia (warstwy, ustawienia kontenera i
sieci, profil seccomp, unit systemd) **celowo NIE trafia do publicznego repo** — żyje w
katalogu **`sandbox/` (git-ignored)** jako dodatkowa warstwa obrony. Publiczne artefakty
(ten proposal, specy, design) opisują **kontrakt i wymagane właściwości**, nie konkretną
konfigurację.

## What Changes

- **Sandbox wykonujący predykaty** implementujący stabilny kontrakt `{code, data} -> bool`
  z change'a `watch-execution` — jako drop-in za punkt wstrzyknięcia ewaluatora w runnerze.
- **Interpreter Starlark** (`starlark-pyo3`, in-process; czysto-pythonowy `starlark-python`
  jako fallback) — deterministyczny, hermetyczny język pythonopodobny. Konwencja wywołania:
  kod definiuje **`def check(data) -> bool`**, host go woła; **wstrzykiwane helpery**
  (`get(data, "a.b", default)`, ewentualnie czasowe) uzupełniają luki językowe.
- **Dwie twarze kontraktu** — ten sam ewaluator obsługuje:
  - *runtime (poll):* `evaluate(code, data) -> true | false | unknown` (błąd → `unknown`,
    treść odrzucona),
  - *authoring:* `validate(code) -> ok | error` oraz `evaluate(code, sample) -> result | error`
    (treść błędu **zwracana**, bo zasila repair-LLM w change'u `watch-authoring`).
- **Warm, długożyjący serwis** w izolowanym kontenerze (rootless Podman), rozmawiający z
  Jarvisem przez **unix socket** (ten sam host — Jarvis i sandbox współdzielą urządzenie).
  Świeże środowisko Starlark na każde wywołanie; limit kroków + wall-clock guard.
- **Właściwości izolacji (wymagania, nie konfiguracja):** brak sieci, użytkownik nie-root,
  read-only, limity zasobów (pamięć/pids), tylko socket jako kanał. Konkretne ustawienia
  realizujące te właściwości są prywatne (`sandbox/`).
- **Publiczna powierzchnia (w repo):** cienki **klient kontraktu** + definicja protokołu
  socketu; niedostępność sandboxa → `unknown` (watch robi skip+retry, nie umiera).
- **Deployment:** obraz budowany raz (kompilacja Rusta/Starlarka na etapie build-image,
  nie runtime), nadzór przez Podman Quadlet (unit prywatny, w `sandbox/`).

## Capabilities

### New Capabilities
- `predicate-sandbox`: izolowany ewaluator predykatów implementujący kontrakt
  `{code, data} -> bool` w dwóch trybach (runtime `bool|unknown`, authoring `error`),
  oparty na deterministycznym/hermetycznym Starlarku z konwencją `def check(data)` i
  wstrzykiwanymi helperami, wystawiony jako warm serwis przez unix socket z wymaganymi
  właściwościami izolacji (brak sieci, non-root, limity), przy czym **konkretna metoda
  zabezpieczenia pozostaje prywatna** (`sandbox/`, git-ignored). Obejmuje też publiczny
  klient kontraktu i zachowanie „niedostępny sandbox → `unknown`".

### Modified Capabilities
<!-- Brak formalnej modyfikacji speców. `predicate-sandbox` DOSTARCZA produkcyjną
     implementację kontraktu, który `conditional-watches` (watch-execution) już opisuje;
     runner wstrzykuje ją zamiast stubu bez zmiany swojej logiki. -->

## Impact

- **Kod prywatny (`sandbox/`, git-ignored):** serwis-ewaluator (Starlark + helpery + tryby
  validate/evaluate), Containerfile + cała konfiguracja hardeningu (sieć, caps, seccomp,
  read-only, limity, non-root), unit Podman Quadlet, wewnętrzny `sandbox/README`/`DESIGN`.
- **Kod publiczny (repo):** klient kontraktu (jak `scheduler_runner` woła socket) +
  definicja protokołu; podmienia in-process stub z `watch-execution` w produkcji.
- **Zależności:** `starlark-pyo3` (Rust/maturin; wheel arm64 lub kompilacja przy build-image)
  albo `starlark-python` (czysty Python) — **wewnątrz obrazu sandboxa**, nie w produkcyjnym
  `poetry.lock` Jarvisa. Podman (rootless) na hoście.
- **Runtime:** brak sieci w kontenerze; komunikacja tylko przez unix socket na współdzielonym
  mount; niedostępność → `unknown`.
- **Testy:** kontrakt (validate/evaluate, `def check`, helpery, `bool`/`error`/`unknown`),
  izolacja (brak sieci/FS z wnętrza), edge (predykat czytający zły klucz → error z treścią).
  Testy hardeningu żyją prywatnie w `sandbox/`.
- **Poza zakresem:** logika watchy/runner/rejestr źródeł (`watch-execution`); generowanie/
  naprawa kodu (`watch-authoring`); microVM/gVisor (na ARM niepraktyczne — granicą jest
  namespace + Starlark).
