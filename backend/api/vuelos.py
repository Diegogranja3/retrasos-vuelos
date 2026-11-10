"""Salidas programadas desde AeroDataBox (vía RapidAPI): descarga con caché y población del modelo.

- Caché cruda en data/cache/api/ (clave: aeropuerto + desde + hasta). Se reutiliza si tiene < 6 h; si la API falla,
  se usa la más reciente que cubra la ventana. Se borra a los 7 días (términos de AeroDataBox).
- Los vuelos fuera de la población del modelo se marcan con un motivo en "excluido"; nunca se borran.
- Del vuelo solo se usan datos de horario (programado), nunca lo ocurrido el día del vuelo.
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from src.features import aeropuertos

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ.parent / "data"  # data/ vive fuera de backend/
HOST = "aerodatabox.p.rapidapi.com"
VIGENCIA = 6 * 3600          # reutilizar respuesta cruda si tiene menos de 6 h
MAX_CRUDO = 7 * 24 * 3600    # AeroDataBox solo permite 7 días de caché cruda
FORMATO = "%Y%m%dT%H%M"      # en nombres de archivo
# BTS cuenta como nacionales los vuelos a territorios de EE. UU. (Puerto Rico, Islas Vírgenes, Guam, ...).
NACIONALES = {"us", "pr", "vi", "gu", "as", "mp"}


class SinDatos(Exception):
    """No hubo respuesta de la API ni caché que cubra la ventana."""


def dir_cache(sub):
    d = Path(os.environ.get("CACHE_DIR", DATA / "cache")) / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def cargar_env():
    # ponytail: lector mínimo de .env (CLAVE=valor), igual que scripts/check_api.py
    env = RAIZ / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                k, v = linea.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def unidades_restantes():
    """Última cabecera X-RateLimit-API-Units-Remaining vista (o None)."""
    ruta = dir_cache("api") / "cuota.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None


def _guardar_cuota(cabeceras):
    for k, v in cabeceras.items():
        if k.lower() == "x-ratelimit-api-units-remaining":
            visto = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            (dir_cache("api") / "cuota.json").write_text(json.dumps({"unidades": int(v), "visto": visto}),
                                                         encoding="utf-8")


def _llamar(aeropuerto, desde, hasta):
    cargar_env()
    llave = os.environ.get("RAPIDAPI_KEY")
    if not llave:
        raise SinDatos("falta RAPIDAPI_KEY en .env")
    url = (f"https://{HOST}/flights/airports/iata/{aeropuerto}/{desde:%Y-%m-%dT%H:%M}/{hasta:%Y-%m-%dT%H:%M}"
           "?direction=Departure")
    req = urllib.request.Request(url, headers={"X-RapidAPI-Key": llave, "X-RapidAPI-Host": HOST})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            _guardar_cuota(r.headers)
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        _guardar_cuota(e.headers)
        raise SinDatos(f"AeroDataBox respondió HTTP {e.code}") from None


def _ventana_archivo(ruta):
    _, d0, d1 = ruta.stem.split("_")
    return datetime.strptime(d0, FORMATO), datetime.strptime(d1, FORMATO)


def obtener(aeropuerto, desde, hasta):
    """Devuelve (JSON de AeroDataBox, fuente) con fuente "api" (recién descargado) o "cache"."""
    archivo = os.environ.get("AERODATABOX_DESDE_ARCHIVO")
    if archivo:  # desarrollo: respuesta guardada, sin gastar cuota
        return json.loads(Path(archivo).read_text(encoding="utf-8")), "cache"
    d = dir_cache("api")
    for p in d.glob("*_*_*.json"):
        if time.time() - p.stat().st_mtime > MAX_CRUDO:
            p.unlink()
    ruta = d / f"{aeropuerto}_{desde:{FORMATO}}_{hasta:{FORMATO}}.json"
    if ruta.exists() and time.time() - ruta.stat().st_mtime < VIGENCIA:
        return json.loads(ruta.read_text(encoding="utf-8")), "cache"
    try:
        cuerpo = _llamar(aeropuerto, desde, hasta)
        datos = json.loads(cuerpo)
        ruta.write_text(cuerpo, encoding="utf-8")
        return datos, "api"
    except (SinDatos, OSError, ValueError) as e:  # sin red, HTTP de error o JSON inválido
        cubren = [p for p in d.glob(f"{aeropuerto}_*_*.json")
                  if _ventana_archivo(p)[0] <= desde and hasta <= _ventana_archivo(p)[1]]
        if cubren:
            p = max(cubren, key=lambda p: p.stat().st_mtime)
            return json.loads(p.read_text(encoding="utf-8")), "cache"
        raise SinDatos(f"AeroDataBox no disponible ({e}) y no hay caché que cubra la ventana") from None


def poblacion(datos, aeropuerto, aerolineas, desde, hasta):
    """Convierte las salidas del JSON en vuelos del contrato, con "excluido" = motivo o None.

    Agrega la clave interna "_salida" (datetime local sin zona) que se quita antes de responder."""
    db = aeropuertos()
    vuelos = []
    for v in datos.get("departures", []):
        m, ap = v["movement"], v["movement"]["airport"]
        local = datetime.fromisoformat(m["scheduledTime"]["local"][:16])
        if not desde <= local <= hasta:
            continue
        iata = ap.get("iata")
        coords = db.get(iata) if iata else None
        lat, lon = (coords["lat"], coords["lon"]) if coords else (ap.get("location", {}).get("lat"),
                                                                ap.get("location", {}).get("lon"))
        aerolinea = (v.get("airline") or {}).get("iata")
        if not v.get("number"):  # aviación privada (p. ej. N500WA): AeroDataBox no trae número
            excluido = "sin número de vuelo"
        elif coords is None:
            excluido = "destino sin coordenadas"
        elif ap.get("countryCode") not in NACIONALES:
            excluido = "vuelo internacional"
        elif v.get("codeshareStatus") == "IsCodeshared":
            excluido = "codeshare duplicado"
        elif not aerolinea or aerolinea not in aerolineas:
            excluido = "aerolínea no vista en entrenamiento"
        else:
            excluido = None
        vuelos.append({
            "id": None, "numero": v.get("number") or v.get("callSign") or "s/n", "aerolinea": aerolinea,
            "aerolinea_nombre": (v.get("airline") or {}).get("name"),
            "destino": {"iata": iata, "nombre": ap.get("name"), "lat": lat, "lon": lon},
            "salida_local": local.strftime("%Y-%m-%dT%H:%M"),
            "salida_utc": m["scheduledTime"]["utc"].replace(" ", "T"),
            "estado": v.get("status"), "probabilidad": None, "prediccion": None, "factores": None, "excluido": excluido,
            "contexto": None, "clima_aproximado": False, "real": {"llegada_retraso_min": None, "retrasado": None},
            "_codeshare": v.get("codeshareStatus"), "_terminal": m.get("terminal"), "_salida": local})

    # Mismo destino, hora UTC y terminal: se conserva el operador (IsOperator) y los demás se marcan.
    # La terminal evita juntar vuelos distintos a la misma hora (en el JSON real: UA/F9 a CLT, AA/UA a DFW,
    # UA/B6 a FLL salen de terminales distintas); un codeshare sale de la terminal del operador.
    grupos = {}
    for v in vuelos:
        if v["destino"]["iata"]:
            grupos.setdefault((v["destino"]["iata"], v["salida_utc"], v["_terminal"]), []).append(v)
    for g in grupos.values():
        queda = next((v for v in g if v["_codeshare"] == "IsOperator"), g[0])
        for v in g:
            if v is not queda and v["excluido"] is None:
                # mismo número = el proveedor repitió el vuelo; número distinto = codeshare
                v["excluido"] = "vuelo duplicado" if v["numero"] == queda["numero"] else "codeshare duplicado"

    vistos = {}
    for v in vuelos:
        base = f"{v['numero'].replace(' ', '')}-{v['_salida']:%Y-%m-%d}"
        vistos[base] = vistos.get(base, 0) + 1
        v["id"] = base if vistos[base] == 1 else f"{base}-{vistos[base]}"
        del v["_codeshare"], v["_terminal"]
    return vuelos
