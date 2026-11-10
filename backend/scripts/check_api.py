"""Fase 0, prueba 1: inspecciona respuestas reales de AeroDataBox (vía RapidAPI).

Uso:
    python scripts/check_api.py                 # consulta la API (cuesta 2 unidades) y guarda el JSON crudo
    python scripts/check_api.py --cache RUTA    # re-analiza un JSON guardado sin gastar cuota

La llave se lee de la variable de entorno RAPIDAPI_KEY (o del archivo .env).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ.parent / "data"  # data/ vive fuera de backend/
DIR_CACHE = DATA / "raw" / "api"
HOST = "aerodatabox.p.rapidapi.com"
AEROPUERTO, TZ = "IAH", ZoneInfo("America/Chicago")
MAX_VUELOS = 30

# Rutas (dentro de cada vuelo) de los campos que necesita el modelo.
# Se llena DESPUÉS de inspeccionar el esquema real impreso por este script; vacío = solo imprime el esquema.
# Origen no viene por vuelo: es el aeropuerto consultado. Duración y distancia no existen en esta respuesta.
CAMPOS = {
    "aerolínea (IATA)": "airline.iata",
    "destino (IATA)": "movement.airport.iata",
    "horario programado (local)": "movement.scheduledTime.local",
    "horario programado (UTC)": "movement.scheduledTime.utc",
    "zona horaria destino": "movement.airport.timeZone",
    "coordenadas destino (lat)": "movement.airport.location.lat",
    "estado codeshare": "codeshareStatus",
    "callSign (operador real)": "callSign",
}


def cargar_env():
    # ponytail: lector mínimo de .env (CLAVE=valor), sin python-dotenv
    env = RAIZ / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def consultar():
    cargar_env()
    llave = os.environ.get("RAPIDAPI_KEY")
    if not llave:
        sys.exit("Falta RAPIDAPI_KEY: cópiala en .env (ver .env.example).")
    # Ventana de 12 h de mañana (hora local de IAH): vuelos programados, como en el escenario de predicción.
    manana = datetime.now(TZ).date() + timedelta(days=1)
    desde, hasta = f"{manana}T06:00", f"{manana}T17:59"
    url = f"https://{HOST}/flights/airports/iata/{AEROPUERTO}/{desde}/{hasta}"
    req = urllib.request.Request(url, headers={"X-RapidAPI-Key": llave, "X-RapidAPI-Host": HOST})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            cuerpo, cabeceras = r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
    DIR_CACHE.mkdir(parents=True, exist_ok=True)
    ruta = DIR_CACHE / f"aerodatabox_{AEROPUERTO}_{datetime.now():%Y%m%d_%H%M%S}.json"
    ruta.write_text(cuerpo, encoding="utf-8")
    cuota = {k: v for k, v in cabeceras.items() if "ratelimit" in k.lower()}
    print(f"URL: {url}\nGuardado en: {ruta}\nCabeceras de cuota: {cuota}\n")
    return json.loads(cuerpo)


def hojas(obj, prefijo=""):
    """Devuelve {ruta: valor} de todas las hojas de un objeto JSON (listas marcadas con [])."""
    if isinstance(obj, dict):
        return {k: v for c, x in obj.items() for k, v in hojas(x, f"{prefijo}.{c}" if prefijo else c).items()}
    if isinstance(obj, list):
        return {k: v for x in obj for k, v in hojas(x, f"{prefijo}[]").items()} or {prefijo + "[]": None}
    return {prefijo: obj}


def vacio(v):
    return v is None or (isinstance(v, str) and not v.strip())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cache", help="JSON guardado a re-analizar (no gasta cuota)")
    args = p.parse_args()
    datos = json.loads(Path(args.cache).read_text(encoding="utf-8")) if args.cache else consultar()

    print("Claves de primer nivel:", {k: type(v).__name__ for k, v in datos.items()})
    listas = {k: v for k, v in datos.items() if isinstance(v, list)}
    for k, v in listas.items():
        print(f"  {k}: {len(v)} elementos")
    # Se analizan las salidas si existen; si no, la primera lista que venga.
    clave = next((k for k in listas if "depart" in k.lower()), next(iter(listas), None))
    if clave is None:
        sys.exit("La respuesta no trae ninguna lista de vuelos; revisa el JSON guardado.")
    vuelos = listas[clave][:MAX_VUELOS]
    print(f"\nAnalizando '{clave}': {len(vuelos)} vuelos\n")

    # Esquema observado: cada ruta, en cuántos vuelos viene con valor y un ejemplo.
    presentes, ejemplo = Counter(), {}
    for v in vuelos:
        for ruta, valor in hojas(v).items():
            if not vacio(valor):
                presentes[ruta] += 1
                ejemplo.setdefault(ruta, valor)
            else:
                presentes[ruta] += 0
    print(f"{'ruta':55} {'con valor':>10}  ejemplo")
    for ruta in sorted(presentes):
        print(f"{ruta:55} {presentes[ruta]:>4}/{len(vuelos):<5}  {str(ejemplo.get(ruta, ''))[:45]}")

    if CAMPOS:
        print(f"\n{'campo necesario':28} {'ruta':45} {'presentes':>10}")
        for campo, ruta in CAMPOS.items():
            n = sum(not vacio(hojas(v).get(ruta)) for v in vuelos)
            print(f"{campo:28} {ruta:45} {n:>4}/{len(vuelos)}")


if __name__ == "__main__":
    main()
