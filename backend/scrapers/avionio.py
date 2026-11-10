"""Scraper de salidas programadas desde Avionio (https://www.avionio.com), fuente complementaria a AeroDataBox.

    python -m scrapers.avionio                       # salidas de IAH que muestra ahora la página (≈ 20 vuelos)
    python -m scrapers.avionio --comparar RUTA.json  # además compara contra un JSON crudo de AeroDataBox

Límites (ver scrapers/README.md):
- Solo se descarga la página por defecto /en/airport/<iata>/departures. Avionio pagina y cambia de hora con
  parámetros (?ts=...&page=...) y su robots.txt prohíbe toda URL con "?" (Disallow: /*?*), así que la ventana
  disponible es la que la página muestra en ese momento (≈ 20 salidas operadas cerca de la hora actual).
- No hay hora de llegada en la tabla de salidas. La "hora de aterrizaje" del historial de cada vuelo no tiene
  zona horaria declarada y resultó incoherente (ver README), por eso no se usa: llegadas = None.
"""
import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import airportsdata

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ.parent / "data"  # data/ vive fuera de backend/
DIR_CACHE = DATA / "cache" / "scrapers"
BASE = "https://www.avionio.com"
FUENTE = "avionio"
# User-agent honesto: dice qué es y para qué; sin correos ni datos personales.
USER_AGENT = "PIA-UANL-prediccion-retrasos/0.1 (proyecto escolar, uso academico no comercial)"
TTL = 3600       # segundos que vale una respuesta en caché
ESPERA = 3.0     # segundos mínimos entre peticiones (robots.txt de Avionio no declara Crawl-delay)
INTENTOS = 3

_ultima_peticion = 0.0
_robots = None


def _zona(iata):
    return ZoneInfo(airportsdata.load("IATA")[iata.upper()]["tz"])


def _descargar(url):
    """GET con caché en disco (TTL 1 h), pausa mínima entre peticiones y reintentos limitados."""
    global _ultima_peticion
    DIR_CACHE.mkdir(parents=True, exist_ok=True)
    archivo = DIR_CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")
    if archivo.exists() and time.time() - archivo.stat().st_mtime < TTL:
        return archivo.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for i in range(INTENTOS):
        time.sleep(max(0.0, _ultima_peticion + ESPERA - time.time()))
        _ultima_peticion = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                texto = r.read().decode("utf-8")
            archivo.write_text(texto, encoding="utf-8")
            return texto
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500 or i == INTENTOS - 1:
                raise  # 403/404 no se reintentan: si nos bloquean, no insistimos
        except urllib.error.URLError:
            if i == INTENTOS - 1:
                raise
        time.sleep(10 * (i + 1))


def _permitido(url):
    """Respeta robots.txt. urllib.robotparser no entiende comodines, así que la regla "Disallow: /*?*" se aplica a mano."""
    global _robots
    if "?" in url:
        return False
    if _robots is None:
        _robots = urllib.robotparser.RobotFileParser()
        _robots.parse(_descargar(BASE + "/robots.txt").splitlines())
    return _robots.can_fetch(USER_AGENT, url)


def _texto(fragmento):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragmento))).strip()


def _celda(fila, clase):
    m = re.search(rf'<td class="{clase}[^"]*"[^>]*>(.*?)</td>', fila, re.S)
    return m.group(1) if m else ""


def parsear_salidas(pagina, aeropuerto="IAH"):
    """Convierte el HTML de la tabla de salidas de Avionio al formato normalizado (horas locales del aeropuerto).

    Las filas "tt-child" son códigos compartidos del vuelo anterior que no es hijo (el que opera).
    """
    zona = _zona(aeropuerto)
    vuelos, operador = [], None
    for clases, fila in re.findall(r'<tr class="tt-row([^"]*)">(.*?)</tr>', pagina, re.S):
        enlace = re.search(r"href='/en/flight/([a-z0-9]{2})-(\d+)'[^>]*data-ts=\"(\d+)\"", _celda(fila, "tt-f"))
        if not enlace:
            continue
        codigo, num, ts = enlace.group(1).upper(), enlace.group(2), int(enlace.group(3))
        # data-ts es la hora programada LOCAL escrita como si fuera UTC (20:00 local -> ...T20:00:00Z), no un instante real.
        salida = datetime.fromtimestamp(ts / 1000, timezone.utc).replace(tzinfo=None)
        estado = _texto(_celda(fila, "tt-s")) or None
        real = None
        m = re.match(r"Departed (\d\d):(\d\d)", estado or "")
        if m:
            real = salida.replace(hour=int(m.group(1)), minute=int(m.group(2)))
            if real - salida > timedelta(hours=12):    # salió antes de medianoche siendo programado después
                real -= timedelta(days=1)
            elif salida - real > timedelta(hours=12):  # programado antes de medianoche, salió después
                real += timedelta(days=1)
        nombre = _texto(re.sub(r"<span.*?</span>", "", _celda(fila, "tt-al"), flags=re.S)) or None
        es_hijo = "tt-child" in clases
        vuelo = {
            "fuente": FUENTE,
            "numero": f"{codigo} {num}",
            "aerolinea": codigo,
            "aerolinea_nombre": nombre,
            "destino_iata": _texto(_celda(fila, "tt-i")) or None,
            "salida_local": salida.strftime("%Y-%m-%dT%H:%M"),
            "salida_utc": salida.replace(tzinfo=zona).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "estado": estado,
            "es_codeshare": es_hijo,
            "operador_iata": operador["aerolinea"] if es_hijo and operador else (None if es_hijo else codigo),
            "codeshares": [],
            "real": {"salida_real_local": real.strftime("%Y-%m-%dT%H:%M") if real else None,
                     "llegada_programada_local": None, "llegada_real_local": None},
        }
        if es_hijo and operador:
            operador["codeshares"].append(vuelo["numero"])
        elif not es_hijo:
            operador = vuelo
        vuelos.append(vuelo)
    return vuelos


def _a_local(dt, zona):
    """datetime sin zona = ya es hora local del aeropuerto; con zona se convierte."""
    return dt.astimezone(zona).replace(tzinfo=None) if dt.tzinfo else dt


def obtener_salidas(aeropuerto="IAH", desde=None, hasta=None):
    """Salidas que Avionio muestra ahora, filtradas a [desde, hasta] (horas locales del aeropuerto).

    Solo cubre la ventana de la página por defecto (≈ 20 vuelos alrededor de la hora actual); si [desde, hasta]
    queda fuera de ella, el resultado es una lista vacía.
    """
    url = f"{BASE}/en/airport/{aeropuerto.lower()}/departures"
    if not _permitido(url):
        raise PermissionError(f"robots.txt de Avionio no permite {url}")
    zona = _zona(aeropuerto)
    vuelos = parsear_salidas(_descargar(url), aeropuerto)
    d = _a_local(desde, zona).strftime("%Y-%m-%dT%H:%M") if desde else ""
    h = _a_local(hasta, zona).strftime("%Y-%m-%dT%H:%M") if hasta else "9999"
    return [v for v in vuelos if d <= v["salida_local"] <= h]


def comparar_con_aerodatabox(vuelos, ruta_json):
    """Cruza por (número, hora local programada) contra las salidas de un JSON crudo de AeroDataBox,
    solo dentro de la ventana de tiempo que cubren ambas fuentes."""
    adb = {(d["number"], d["movement"]["scheduledTime"]["local"][:16].replace(" ", "T")): d
           for d in json.loads(Path(ruta_json).read_text(encoding="utf-8"))["departures"]}
    av = {(v["numero"], v["salida_local"]): v for v in vuelos}
    if not adb or not av:
        return {"solapamiento": None}
    ini = max(min(k[1] for k in adb), min(k[1] for k in av))
    fin = min(max(k[1] for k in adb), max(k[1] for k in av))
    if ini > fin:
        return {"solapamiento": None, "avionio": [min(k[1] for k in av), max(k[1] for k in av)],
                "aerodatabox": [min(k[1] for k in adb), max(k[1] for k in adb)]}
    a = {k for k in av if ini <= k[1] <= fin}
    b = {k for k in adb if ini <= k[1] <= fin}
    # Avionio lista los regionales con el número del operador (OO 4998) y AeroDataBox con el comercial (UA 4998):
    # mismo número sin prefijo y misma hora cuenta como el mismo vuelo.
    digitos = lambda ks: {(n.split()[-1], h) for n, h in ks}
    return {"solapamiento": [ini, fin], "coinciden": len(a & b),
            "coinciden_sin_prefijo": len(digitos(a) & digitos(b)),
            "solo_scraper": sorted(a - b), "solo_aerodatabox": sorted(b - a)}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--aeropuerto", default="IAH")
    p.add_argument("--comparar", metavar="JSON_AERODATABOX")
    a = p.parse_args()
    salidas = obtener_salidas(a.aeropuerto)
    sys.stdout.reconfigure(encoding="utf-8")
    for v in salidas:
        print(v["salida_local"], v["numero"].ljust(8), v["destino_iata"], "codeshare" if v["es_codeshare"] else "opera",
              v["estado"])
    print(f"{len(salidas)} salidas ({sum(not v['es_codeshare'] for v in salidas)} operadas, resto codeshare)")
    if a.comparar:
        print(json.dumps(comparar_con_aerodatabox(salidas, a.comparar), ensure_ascii=False, indent=1))
