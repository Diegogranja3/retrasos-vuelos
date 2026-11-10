"""Salidas desde Airportia (https://www.airportia.com). SUS TÉRMINOS PROHÍBEN EL SCRAPING; ver scrapers/README.md.

    SCRAPERS_SIN_PERMISO=1 python -m scrapers.airportia

Una sola página: /united-states/<aeropuerto>/departures/ (≈ 20 vuelos operados, ~2 h desde la hora actual).
La tabla trae fecha, hora programada y hora real de salida (columna "Departure"), en hora local del aeropuerto.
"""
import re
import sys
from datetime import datetime, timedelta, timezone

from scrapers.avionio import _texto, _zona
from scrapers.sin_permiso import filtrar, obtener

FUENTE = "airportia"
BASE = "https://www.airportia.com"
# Airportia usa un "slug" por aeropuerto que no se puede deducir del código IATA.
PAGINAS = {"IAH": "/united-states/george-bush-intercontinental-houston-airport/departures/"}


def _hora(fecha, hhmm):
    h, m = map(int, hhmm.split(":"))
    return fecha.replace(hour=h, minute=m)


def parsear_salidas(pagina, aeropuerto, descarga):
    """`descarga` (datetime) solo sirve para deducir el año, que la tabla no muestra ("Oct 28")."""
    zona = _zona(aeropuerto)
    hoy = descarga.astimezone(zona).replace(tzinfo=None)
    vuelos, operador = [], None
    for tipo, fila in re.findall(r'<tr class="flightsTable-(parent|child)Flight">(.*?)</tr>', pagina, re.S):
        celdas = [_texto(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", fila, re.S)]
        num = re.search(r'<a href="/flights/[^"]+" title="([A-Z0-9]{2}) (\d+[A-Z]?)"', fila)
        if not num or len(celdas) < 7 or not re.fullmatch(r"\d\d:\d\d", celdas[4]):
            continue
        # Año: el que deja la fecha más cerca del día de descarga (cubre el cambio de año).
        dia = min((datetime.strptime(f"{celdas[3]} {a}", "%b %d %Y") for a in (hoy.year - 1, hoy.year, hoy.year + 1)),
                  key=lambda d: abs(d - hoy))
        salida = _hora(dia, celdas[4])
        real = None
        if re.fullmatch(r"\d\d:\d\d", celdas[5]):
            real = _hora(dia, celdas[5])
            if real - salida > timedelta(hours=12):
                real -= timedelta(days=1)
            elif salida - real > timedelta(hours=12):
                real += timedelta(days=1)
        destino = re.search(r"\b([A-Z]{3})$", celdas[1])
        es_hijo = tipo == "child"
        vuelo = {
            "fuente": FUENTE,
            "numero": f"{num.group(1)} {num.group(2)}",
            "aerolinea": num.group(1),
            "aerolinea_nombre": celdas[2] or None,
            "destino_iata": destino.group(1) if destino else None,
            "salida_local": salida.strftime("%Y-%m-%dT%H:%M"),
            "salida_utc": salida.replace(tzinfo=zona).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "estado": celdas[6] or None,
            "es_codeshare": es_hijo,
            # La fila padre es el vuelo que opera (p. ej. OO 5476 SkyWest); las hijas, sus códigos compartidos.
            "operador_iata": operador["aerolinea"] if es_hijo and operador else (None if es_hijo else num.group(1)),
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


def obtener_salidas(aeropuerto="IAH", desde=None, hasta=None):
    aeropuerto = aeropuerto.upper()
    if aeropuerto not in PAGINAS:
        raise ValueError(f"Falta la página de Airportia para {aeropuerto} en PAGINAS")
    vuelos = obtener(FUENTE, BASE + PAGINAS[aeropuerto], lambda t, d: parsear_salidas(t, aeropuerto, d))
    return filtrar(vuelos, desde, hasta, _zona(aeropuerto))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    v = obtener_salidas()
    for x in v:
        print(x["salida_local"], x["numero"].ljust(8), x["destino_iata"], x["estado"], x["real"]["salida_real_local"])
    print(f"{len(v)} salidas ({sum(not x['es_codeshare'] for x in v)} operadas)")
