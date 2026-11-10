"""Scrapers de salidas de vuelos (fuente complementaria a AeroDataBox). Ver scrapers/README.md.

Por defecto se usa Avionio, el único sitio útil cuyo robots.txt y condiciones lo permiten. flightradar24, airportia y
flightaware tienen el mismo `obtener_salidas`, pero sus términos prohíben el scraping y solo corren con
SCRAPERS_SIN_PERMISO=1 (decisión del usuario para este proyecto escolar):
    from scrapers import obtener_salidas
    vuelos = obtener_salidas("IAH", desde, hasta)   # horas locales del aeropuerto
"""


def obtener_salidas(aeropuerto="IAH", desde=None, hasta=None):
    # Importación diferida: así `python -m scrapers.avionio` no carga el módulo dos veces.
    from scrapers import avionio
    return avionio.obtener_salidas(aeropuerto, desde, hasta)
