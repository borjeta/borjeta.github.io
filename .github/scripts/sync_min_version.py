#!/usr/bin/env python3
"""
Sube `minVersion` de `huerto/version.json` cuando una versión nueva YA está publicada en la App Store.

Para qué: la app (src/features/update/) bloquea al abrirse si su versión es inferior a `minVersion`. Sin este script habría
que subir `minVersion` a mano en cada release, y hacerlo ANTES de que la versión esté publicada dejaría a los usuarios
bloqueados sin poder actualizar. Aquí se consulta la API pública de la App Store (`itunes.apple.com/lookup`), que solo
devuelve versiones ya publicadas, y se sube `minVersion` a esa versión: el usuario ve el aviso cuando ya puede actualizar.
Es la versión «en el servidor» de la regla de actualización forzada automática: cubre a la 1.0.0, cuyo binario solo sabe
leer `minVersion`. Las versiones que lleven el forzado automático dentro de la app no lo necesitan, pero no estorba.

Salvaguardas (todas comprobadas por --selftest):
  - Solo SUBE. Nunca baja `minVersion`.
  - Falla en abierto: sin respuesta, con error, sin resultados (app aún no publicada), con una versión que no sea `x`,
    `x.y` o `x.y.z`, o con un `trackId` que no sea el de la app, NO se toca nada.
  - Interruptor de emergencia: `"autoForce": false` en version.json PAUSA las subidas automáticas.
  - Espera HORAS_ESPERA (4) desde `currentVersionReleaseDate`, para no bloquear mientras la tienda propaga la versión.
  - No fuerza si la versión nueva exige un iOS mínimo MAYOR que el de la versión anterior (`minimumOsVersion`): esos
    usuarios no podrían actualizar y quedarían bloqueados. En ese caso hay que decidirlo a mano.
  - Compara numéricamente (1.10.0 > 1.9.0), igual que la app (src/features/update/version.ts).
  - Conserva el resto de claves de version.json.

Se ejecuta desde un workflow programado de GitHub Actions del repo `borjeta.github.io`. Solo usa la biblioteca estándar.

Uso:
    python3 sync_min_version.py [--dry-run] [--file huerto/version.json] [--status huerto/store-sync.json]
    python3 sync_min_version.py --store-version 1.0.1 --dry-run     # PRUEBAS: simula una versión de la tienda
    python3 sync_min_version.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_ID = "6837435714"  # Apple ID de la ficha (src/config/app.ts, APP_STORE_URL)
COUNTRY = "us"  # la versión es la misma en todos los países
HORAS_ESPERA = 4  # regla del usuario: esperar N horas (4 por defecto) desde currentVersionReleaseDate
LOOKUP = "https://itunes.apple.com/lookup?id={id}&country={country}&t={t}"
STRICT_VERSION = re.compile(r"^(\d{1,9})(?:\.(\d{1,9}))?(?:\.(\d{1,9}))?$")

Version = tuple[int, int, int]


def parse(raw: object) -> Version | None:
    """`x`, `x.y` o `x.y.z` (solo dígitos) -> tupla; cualquier otra cosa (sufijos, letras, vacío, no texto) -> None."""
    if not isinstance(raw, str):
        return None
    m = STRICT_VERSION.match(raw.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0))


def fmt(v: Version) -> str:
    return f"{v[0]}.{v[1]}.{v[2]}"


def parse_date(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        d = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def decide(
    store: object,
    current: object,
    *,
    auto_force: object = True,
    released: datetime | None,
    now: datetime,
    min_os: object,
    baseline_os: object,
    wait_hours: float = HORAS_ESPERA,
) -> tuple[str | None, str]:
    """(nueva minVersion x.y.z o None, motivo). Solo devuelve una versión si TODAS las salvaguardas se cumplen."""
    s = parse(store)
    if s is None:
        return None, "no hay una versión publicada válida"
    c = parse(current)  # ilegible o ausente = nada exigido todavía
    if c is not None and s <= c:
        return None, f"la versión publicada ({fmt(s)}) no es mayor que minVersion ({fmt(c)})"
    if auto_force is False:
        return None, "PAUSADO: autoForce=false en version.json (interruptor de emergencia)"
    if released is None:
        return None, "sin currentVersionReleaseDate válida: no se fuerza (falla en abierto)"
    espera = timedelta(hours=wait_hours)
    if now - released < espera:
        falta = espera - (now - released)
        return None, f"publicada hace poco: se espera {wait_hours:g} h desde la publicación (faltan {falta})"
    new_os, old_os = parse(min_os), parse(baseline_os)
    if new_os is None or old_os is None:
        return None, "sin referencia del iOS mínimo (nuevo o anterior): no se fuerza solo, decídelo a mano"
    if new_os > old_os:
        return None, f"la versión nueva exige iOS {fmt(new_os)} (antes {fmt(old_os)}): hay usuarios que no podrían actualizar; decídelo a mano"
    return fmt(s), "ok"


def fetch_store(app_id: str = APP_ID, country: str = COUNTRY, tries: int = 3) -> tuple[dict | None, str]:
    """({version, released, min_os} o None, explicación). Nunca lanza: cualquier problema devuelve None."""
    last = "sin intentos"
    for i in range(tries):
        url = LOOKUP.format(id=app_id, country=country, t=int(time.time()))
        req = urllib.request.Request(url, headers={"User-Agent": "min-version-sync/1.0", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (URL fija, https)
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last = f"error de red o de formato: {exc}"
            time.sleep(3 * (i + 1))
            continue
        results = data.get("results") if isinstance(data, dict) else None
        if not results:
            return None, "la App Store no devuelve la app (todavía no está publicada)"
        first = results[0] if isinstance(results, list) and isinstance(results[0], dict) else {}
        if str(first.get("trackId")) != str(app_id):
            return None, f"trackId inesperado ({first.get('trackId')!r}): se ignora"
        if parse(first.get("version")) is None:
            return None, f"versión con formato no admitido ({first.get('version')!r}): se ignora"
        return (
            {
                "version": str(first["version"]).strip(),
                "released": parse_date(first.get("currentVersionReleaseDate")),
                "min_os": first.get("minimumOsVersion"),
            },
            "ok",
        )
    return None, last


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def dump_inline(data: dict) -> str:
    """Mismo formato que el fichero original: `{ "minVersion": "1.0.0", "autoForce": true }`."""
    return "{ " + ", ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in data.items()) + " }\n"


def set_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def run(args: argparse.Namespace) -> int:
    version_file, status_file = Path(args.file), Path(args.status)
    now = datetime.now(timezone.utc)
    cfg = read_json(version_file)
    status = read_json(status_file)
    current = cfg.get("minVersion") if isinstance(cfg.get("minVersion"), str) else None
    baseline_os = status.get("minimumOsVersion")

    simulated = bool(args.store_version)
    if simulated:
        info = {
            "version": args.store_version,
            "released": now - timedelta(hours=args.sim_hours_ago),
            "min_os": args.sim_min_os or baseline_os,
        }
        why = f"SIMULADA (pruebas): publicada hace {args.sim_hours_ago:g} h, iOS mínimo {info['min_os']}"
    else:
        info, why = fetch_store(args.app_id, args.country)

    print(f"Versión publicada en la App Store: {info['version'] if info else 'ninguna'} ({why})")
    print(f"minVersion actual: {current or 'ilegible'} | autoForce: {cfg.get('autoForce', 'no definido (activo)')} | iOS mínimo de referencia: {baseline_os or 'sin referencia'}")

    new_min, motivo = (None, "sin datos de la tienda")
    if info:
        new_min, motivo = decide(
            info["version"],
            current,
            auto_force=cfg.get("autoForce", True),
            released=info["released"],
            now=now,
            min_os=info["min_os"],
            baseline_os=baseline_os,
        )
    print(f"Decisión: {'SUBIR minVersion a ' + new_min if new_min else 'no tocar nada'} ({motivo})")

    changed = False
    if new_min and not args.dry_run:
        cfg["minVersion"] = new_min
        version_file.write_text(dump_inline(cfg), encoding="utf-8", newline="\n")
        print(f"minVersion sube de {current} a {new_min}")
        changed = True
    elif new_min:
        print(f"[dry-run] no se escribe nada")

    # Estado público y mínimo (sin fechas, para que solo cambie cuando hay novedades): qué vio el vigilante.
    if not args.dry_run and not simulated:
        final_min = new_min or current
        enforced_is_store = bool(info) and parse(final_min) is not None and parse(info["version"]) == parse(final_min)
        state = {
            "storeVersion": fmt(parse(info["version"])) if info else None,
            "minVersion": final_min,
            # iOS mínimo de la versión que se está exigiendo: referencia para la siguiente
            "minimumOsVersion": (info["min_os"] if enforced_is_store else baseline_os) if info else baseline_os,
        }
        text = json.dumps(state, indent=2) + "\n"
        old = status_file.read_text(encoding="utf-8") if status_file.exists() else None
        if old != text:
            status_file.write_text(text, encoding="utf-8", newline="\n")
            changed = True
            print(f"Estado actualizado en {status_file}")

    set_output("changed", "true" if changed else "false")
    set_output("message", f"minVersion {new_min}" if new_min and not args.dry_run else "estado del vigilante")
    return 0


def selftest() -> int:
    now = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
    h = lambda n: now - timedelta(hours=n)  # noqa: E731

    def case(store, cur, **kw):
        base = dict(auto_force=True, released=h(10), now=now, min_os="16.4", baseline_os="16.4")
        base.update(kw)
        return decide(store, cur, **base)[0]

    cases = [
        # (descripción, resultado, esperado)
        ("misma versión: nada", case("1.0.0", "1.0.0"), None),
        ("parche nuevo: sube", case("1.0.1", "1.0.0"), "1.0.1"),
        ("menor nueva: sube", case("1.1.0", "1.0.5"), "1.1.0"),
        ("normaliza x.y", case("2.0", "1.9.9"), "2.0.0"),
        ("comparación NUMÉRICA 1.10.0 > 1.9.0", case("1.10.0", "1.9.0"), "1.10.0"),
        ("nunca baja", case("1.9.0", "1.10.0"), None),
        ("tienda menor que minVersion", case("0.9.0", "1.0.0"), None),
        ("fichero ilegible: la publicada es segura", case("1.0.0", None), "1.0.0"),
        ("fichero con basura", case("1.0.0", "basura"), "1.0.0"),
        ("sin respuesta", case(None, "1.0.0"), None),
        ("versión vacía", case("", "1.0.0"), None),
        ("versión con letras", case("abc", "1.0.0"), None),
        ("1.2.x", case("1.2.x", "1.0.0"), None),
        ("sufijo beta no admitido", case("1.0.1-beta", "1.0.0"), None),
        ("prefijo v no admitido", case("v1.0.1", "1.0.0"), None),
        ("no es texto", case(101, "1.0.0"), None),
        ("número desmesurado", case("1." + "9" * 12, "1.0.0"), None),
        ("autoForce=false PAUSA", case("1.0.1", "1.0.0", auto_force=False), None),
        ("autoForce=true sube", case("1.0.1", "1.0.0", auto_force=True), "1.0.1"),
        ("autoForce ausente (por defecto activo)", decide("1.0.1", "1.0.0", released=h(10), now=now, min_os="16.4", baseline_os="16.4")[0], "1.0.1"),
        ("publicada hace 1 h: espera", case("1.0.1", "1.0.0", released=h(1)), None),
        ("publicada hace 3 h 59 min: espera", case("1.0.1", "1.0.0", released=now - timedelta(hours=3, minutes=59)), None),
        ("publicada hace 4 h exactas: sube", case("1.0.1", "1.0.0", released=h(4)), "1.0.1"),
        ("sin fecha de publicación: no fuerza", case("1.0.1", "1.0.0", released=None), None),
        ("exige iOS mayor: no fuerza", case("1.0.1", "1.0.0", min_os="17.0", baseline_os="16.4"), None),
        ("mismo iOS: sube", case("1.0.1", "1.0.0", min_os="16.4", baseline_os="16.4"), "1.0.1"),
        ("iOS mínimo menor: sube", case("1.0.1", "1.0.0", min_os="15.1", baseline_os="16.4"), "1.0.1"),
        ("iOS mínimo ilegible: no fuerza", case("1.0.1", "1.0.0", min_os=None), None),
        ("sin iOS de referencia: no fuerza", case("1.0.1", "1.0.0", baseline_os=None), None),
        ("fecha futura (reloj raro): espera", case("1.0.1", "1.0.0", released=now + timedelta(hours=2)), None),
    ]
    fails = 0
    for desc, got, expected in cases:
        ok = got == expected
        fails += 0 if ok else 1
        print(("OK   " if ok else "FALLO"), f"{desc}: {got!r} (esperado {expected!r})")
    # fechas
    for raw, ok in [("2026-11-22T10:00:00Z", True), ("2026-11-22T10:00:00+00:00", True), ("2026-11-22", True), ("ayer", False), (None, False), (5, False)]:
        got = parse_date(raw) is not None
        fails += 0 if got == ok else 1
        print(("OK   " if got == ok else "FALLO"), f"fecha {raw!r}: {'válida' if got else 'no válida'}")
    # formato del fichero
    f = dump_inline({"minVersion": "1.0.1", "autoForce": True})
    ok = f == '{ "minVersion": "1.0.1", "autoForce": true }\n' and json.loads(f) == {"minVersion": "1.0.1", "autoForce": True}
    fails += 0 if ok else 1
    print(("OK   " if ok else "FALLO"), f"formato de version.json: {f.strip()}")
    total = len(cases) + 6 + 1
    print(f"\n{total - fails}/{total} comprobaciones correctas")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default="huerto/version.json")
    ap.add_argument("--status", default="huerto/store-sync.json")
    ap.add_argument("--app-id", default=APP_ID)
    ap.add_argument("--country", default=COUNTRY)
    ap.add_argument("--store-version", default="", help="PRUEBAS: simula esta versión de la tienda (no escribe el estado)")
    ap.add_argument("--sim-hours-ago", type=float, default=100.0, help="PRUEBAS: horas desde la publicación simulada")
    ap.add_argument("--sim-min-os", default="", help="PRUEBAS: iOS mínimo de la versión simulada (por defecto, el de referencia)")
    ap.add_argument("--dry-run", action="store_true", help="solo muestra qué haría")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    return selftest() if args.selftest else run(args)


if __name__ == "__main__":
    sys.exit(main())
