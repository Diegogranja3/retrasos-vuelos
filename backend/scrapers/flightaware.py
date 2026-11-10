"""Salidas desde FlightAware (web). SUS TÉRMINOS PROHÍBEN EL SCRAPING; ver scrapers/README.md.

Estado (2026-10-28): BLOQUEADO. /live/airport/KIAH/departures responde 301 a https://login.flightaware.com/signin
para nuestro user-agent, y no iniciamos sesión. Como nunca se vio la tabla, no hay parser: si algún día la página
responde con datos, se registra "la página no contiene vuelos" y hay que escribir el parseo con un fixture real.
"""
import airportsdata

from scrapers.sin_permiso import obtener

FUENTE = "flightaware"


def obtener_salidas(aeropuerto="IAH", desde=None, hasta=None):
    icao = airportsdata.load("IATA")[aeropuerto.upper()]["icao"]
    return obtener(FUENTE, f"https://www.flightaware.com/live/airport/{icao}/departures", lambda t, d: [])


if __name__ == "__main__":
    print(f"{len(obtener_salidas())} salidas")
