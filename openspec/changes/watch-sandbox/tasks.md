## 1. Spike wykonawczy: interpreter na ARM

- [x] 1.1 Zbudować `starlark-pyo3` na arm64 (wheel lub kompilacja przez buildx w obrazie);
      zmierzyć czas/rozmiar builda. Zapisać werdykt w `sandbox/DESIGN` (prywatnie).
- [x] 1.2 Potwierdzić API na realnym kodzie: `parse` → `Module()` + `mod["data"]=…` →
      `add_callable("get", …)` → `eval` → `freeze().call("check", data)` → `bool`.
- [x] 1.3 Jeśli build arm64 boli — zwalidować fallback `starlark-python` (czysty Python) na
      tym samym kontrakcie; podjąć twardą decyzję interpretera.

## 2. Serwis-ewaluator (prywatnie, `sandbox/`)

- [x] 2.1 Ewaluator: przyjmuje `code` + `data`, składa `Module` ze świeżym środowiskiem,
      wstrzykuje helpery, wykonuje `def check(data)`, zwraca `bool`. Świeży `Module()` per call.
- [x] 2.2 Helpery v1: `get(data, "a.b.c", default)` (bezpieczny dostęp po ścieżce); miejsce na
      ewentualne helpery czasowe. Czyste, nie przyjmują kodu.
- [x] 2.3 Dwa tryby: `evaluate(code, data)` (runtime: `bool`, wyjątek→sygnał `unknown`) oraz
      `validate(code)` / `evaluate(code, sample)` (authoring: zwracają **treść błędu** interpretera).
- [x] 2.4 Limity: limit kroków starlark-rust + wall-clock guard; runaway/timeout → błąd/`unknown`.

## 3. Kontrakt: protokół socketu + publiczny klient (w repo)

- [x] 3.1 Zdefiniować protokół unix-socket (żądanie: `mode`, `code`, `data`; odpowiedź:
      `result | error`); wersjonowanie/ramkowanie. Udokumentować jako publiczne źródło prawdy.
- [x] 3.2 Publiczny klient kontraktu: `evaluate(code, data) -> true|false|unknown` (runtime) i
      `validate/evaluate_with_error` (authoring), rozmawiający z socketem; timeout/niedostępność → `unknown`.
- [x] 3.3 Punkt integracji: klient implementuje ten sam interfejs co in-process stub z
      `watch-execution`, by podmiana stub→klient nie ruszała logiki runnera.

## 4. Kontener + hardening (prywatnie, `sandbox/`)

- [x] 4.1 Containerfile budujący obraz z interpreterem (kompilacja przy build-image); non-root user.
- [x] 4.2 Konfiguracja izolacji realizująca WYMAGANE właściwości: brak sieci, read-only rootfs
      (+ zapisywalny mount tylko na socket), limity pamięci/pids, drop capabilities, no-new-privileges,
      profil seccomp. Konkretne wartości/profil — w `sandbox/`, nie w publicznym repo.
- [x] 4.3 Rootless Podman; weryfikacja z wnętrza: brak dostępu do sieci i FS hosta; działa tylko socket.

## 5. Nadzór i deployment (prywatnie, `sandbox/`)

- [x] 5.1 Podman Quadlet (`.container`) → systemd user unit: auto-restart, start przy bootcie.
- [x] 5.2 `sandbox/README` (prywatnie): jak zbudować obraz, wdrożyć unit, gdzie jest socket,
      jak zweryfikować izolację. `sandbox/DESIGN`: warstwy i decyzje hardeningu.
- [x] 5.3 Potwierdzić, że **cały** `sandbox/` jest git-ignored (nic z metody nie wycieka do repo).

## 6. Integracja z runtime

- [x] 6.1 Przełącznik/konfiguracja wyboru ewaluatora: in-process stub (dev) vs klient socketu (prod).
- [ ] 6.2 End-to-end: watch z `watch-execution` przechodzi na `true` przez prawdziwy sandbox → dostarcza
      raz; sandbox zatrzymany → odczyt `unknown`, watch aktywny.

## 7. Testy

- [x] 7.1 Kontrakt: `def check` + helpery, `true/false`, `validate` błędnego kodu → treść błędu,
      `evaluate(code, sample)` → wynik. (Testy publicznego klienta z fake-serwisem/realnym socketem.)
- [x] 7.2 Izolacja (prywatnie, `sandbox/`): próba I/O/importu/sieci z predykatu → porażka w runtime;
      runaway → przerwane; świeże środowisko per-call (brak wycieku stanu).
- [x] 7.3 Niedostępność serwisu → `unknown` (nie `false`) po stronie klienta.
- [x] 7.4 Brak regresji: produkcyjny `poetry.lock` Jarvisa bez `starlark`/Podman (żyją w obrazie).
