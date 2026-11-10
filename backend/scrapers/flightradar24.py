"""Salidas desde Flightradar24 (web). SUS TÉRMINOS PROHÍBEN EL SCRAPING; ver scrapers/README.md.

    SCRAPERS_SIN_PERMISO=1 python -m scrapers.flightradar24

Una sola página: /data/airports/<iata>/departures (≈ 100 vuelos operados, unas 11 h desde la hora actual).
Se lee el HTML que el servidor ya trae; no se usa ninguna API interna ni token.
La tabla no muestra fechas: se deducen del momento de descarga y de cuándo la hora "da la vuelta" (PM -> AM).
"""
import re
import sys
from datetime import datetime, timedelta, timezone

from scrapers.avionio import _texto, _zona
from scrapers.sin_permiso import filtrar, obtener

FUENTE = "flightradar24"
BASE = "https://www.flightradar24.com"


def _hora(texto):
    return datetime.strptime(texto.strip(), "%I:%M %p").time()


def _numero(texto):
    """'Y4731' -> ('Y4', '731'). Matrículas o indicativos ('N170XY', 'PWR10') -> (None, texto)."""
    m = re.fullmatch(r"([A-Z0-9]{2})(\d{1,4}[A-Z]?)", texto)
    if m and not m.group(1).isdigit():
        return m.group(1), m.group(2)
    return None, texto


def parsear_salidas(pagina, aeropuerto, descarga):
    zona = _zona(aeropuerto)
    pagina = re.sub(r"<svg.*?</svg>|<!--.*?-->", "", pagina, flags=re.S)
    previa = descarga.astimezone(zona).replace(tzinfo=None, second=0, microsecond=0)
    vuelos = []
    for bloque in re.split(r'<div data-flight-id="\d+">', pagina)[1:]:
        # Cada bloque tiene versión de escritorio y de móvil; solo se lee la de escritorio (va primero).
        hora = re.search(r'text-l2-strong text-semantic-text-primary">(\d{1,2}:\d\d [AP]M)<', bloque)
        nombre = re.search(r'aria-label="Show details for ([^"]+)"', bloque)
        if not hora or not nombre:
            continue
        # Primera fila: el día de la descarga (o el anterior si quedaría >12 h en el futuro). Después, la lista va
        # en orden: si la hora retrocede más de 12 h es que pasó la medianoche.
        salida = datetime.combine(previa.date(), _hora(hora.group(1)))
        if not vuelos and salida - previa > timedelta(hours=12):
            salida -= timedelta(days=1)
        while salida < previa - timedelta(hours=12):
            salida += timedelta(days=1)
        previa = salida
        codigo, num = _numero(nombre.group(1))
        operador = re.search(r'/data/airlines/([a-z0-9]{2})-[a-z0-9]+" aria-label="([^"]+)"', bloque)
        destino = re.search(r'leading-none">([A-Z0-9]{3})</span>', bloque)
        estado = re.search(r'text-sm">([^<]+)<', bloque)
        estado = estado.group(1).strip() if estado else None
        real = re.match(r"Departed (\d{1,2}:\d\d [AP]M)", estado or "")
        if real:
            real = datetime.combine(salida.date(), _hora(real.group(1)))
            if real - salida > timedelta(hours=12):
                real -= timedelta(days=1)
            elif salida - real > timedelta(hours=12):
                real += timedelta(days=1)
        # Tooltip "Also marketed as": <li><span>F96731</span><a ...><span ...>Frontier</span></a></li>
        codeshares = [(n, _texto(nom) or None) for n, nom in
                      re.findall(r'<li[^>]*><span class="whitespace-nowrap[^"]*">([^<]+)</span>(.*?)</li>', bloque, re.S)]
        base = {
            "fuente": FUENTE,
            "destino_iata": destino.group(1) if destino else None,
            "salida_local": salida.strftime("%Y-%m-%dT%H:%M"),
            "salida_utc": salida.replace(tzinfo=zona).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "estado": estado,
            # La columna "Airline" enlaza /data/airlines/<iata>-<icao>: es la aerolínea que FR24 muestra como
            # operadora (para United Express muestra United, no SkyWest/Mesa).
            "operador_iata": operador.group(1).upper() if operador else None,
            "real": {"salida_real_local": real.strftime("%Y-%m-%dT%H:%M") if real else None,
                     "llegada_programada_local": None, "llegada_real_local": None},
        }
        hijos = [(*_numero(c), nom) for c, nom in codeshares]
        vuelos.append({**base, "numero": f"{codigo} {num}" if codigo else num, "aerolinea": codigo,
                       "aerolinea_nombre": operador.group(2) if operador else None, "es_codeshare": False,
                       "codeshares": [f"{c} {n}" if c else n for c, n, _ in hijos]})
        for c, n, nom in hijos:
            vuelos.append({**base, "numero": f"{c} {n}" if c else n, "aerolinea": c, "aerolinea_nombre": nom,
                           "es_codeshare": True, "codeshares": [], "real": dict(base["real"])})
    return vuelos


def obtener_salidas(aeropuerto="IAH", desde=None, hasta=None):
    url = f"{BASE}/data/airports/{aeropuerto.lower()}/departures"
    vuelos = obtener(FUENTE, url, lambda t, d: parsear_salidas(t, aeropuerto, d))
    return filtrar(vuelos, desde, hasta, _zona(aeropuerto))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    v = obtener_salidas()
    for x in v:
        print(x["salida_local"], x["numero"].ljust(8), x["destino_iata"], x["operador_iata"], x["estado"])
    print(f"{len(v)} salidas ({sum(not x['es_codeshare'] for x in v)} operadas)")
