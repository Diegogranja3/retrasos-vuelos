"""Descarga compartida para los sitios cuyos términos PROHÍBEN el acceso automatizado (FlightAware, Flightradar24, Airportia).

El usuario decidió usarlos, asumiendo el riesgo, solo para este proyecto escolar sin fines de lucro (ver scrapers/README.md).
Por eso están desactivados salvo que SCRAPERS_SIN_PERMISO=1, y las reglas son estrictas:
- Peticiones HTTP normales a páginas públicas, con el mismo user-agent honesto; sin sesión, sin proxies, sin headless.
- Mínimo 10 s entre peticiones al mismo sitio y como máximo 3 páginas por sitio en cada ejecución.
- Caché de 1 h en data/cache/scrapers/.
- Si la respuesta es 403/429, cualquier redirección (p. ej. a un login), es una página de reto/captcha o no trae datos: se registra
  "bloqueado", se avisa y se regresa [] sin reintentar ni intentar rodear el bloqueo.
"""
import hashlib
import os
import time
import urllib.error
import urllib.request
import warnings
from datetime import datetime

from scrapers.avionio import DIR_CACHE, TTL, USER_AGENT

ESPERA = 10.0
MAX_PAGINAS = 3
REGISTRO = DIR_CACHE / "bloqueos.log"
# Marcas de páginas de reto. No se usa "captcha" a secas: FR24 y Airportia cargan reCAPTCHA en páginas normales.
RETOS = ("<title>Just a moment", "cf-chl-", "Attention Required! | Cloudflare", "awswaf", "px-captcha",
         "Please verify you are a human")

_ultima = {}    # sitio -> momento de la última petición
_paginas = {}   # sitio -> páginas descargadas en esta ejecución


class Bloqueado(Exception):
    pass


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    """No seguir redirecciones: FlightAware manda a su login y ni siquiera queremos pedir esa página."""
    def redirect_request(self, *args, **kwargs):
        return None


_abrir = urllib.request.build_opener(_SinRedirecciones).open


def habilitado(sitio):
    if os.environ.get("SCRAPERS_SIN_PERMISO") == "1":
        return True
    warnings.warn(f"{sitio}: desactivado (sus términos prohíben el acceso automatizado). Usa SCRAPERS_SIN_PERMISO=1.")
    return False


def bloqueado(sitio, url, motivo):
    """Registra el bloqueo en data/cache/scrapers/bloqueos.log y avisa. Quien llama regresa []."""
    DIR_CACHE.mkdir(parents=True, exist_ok=True)
    with REGISTRO.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{sitio}\tbloqueado\t{motivo}\t{url}\n")
    warnings.warn(f"{sitio}: bloqueado ({motivo}); se regresa [] sin reintentar.")


def descargar(sitio, url):
    """Regresa (html, momento_de_descarga) desde la caché o con UNA petición. Lanza Bloqueado si algo huele a bloqueo."""
    DIR_CACHE.mkdir(parents=True, exist_ok=True)
    archivo = DIR_CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")
    if archivo.exists() and time.time() - archivo.stat().st_mtime < TTL:
        return archivo.read_text(encoding="utf-8"), datetime.fromtimestamp(archivo.stat().st_mtime).astimezone()
    if _paginas.get(sitio, 0) >= MAX_PAGINAS:
        raise Bloqueado(f"límite de {MAX_PAGINAS} páginas por ejecución alcanzado")
    time.sleep(max(0.0, _ultima.get(sitio, 0.0) + ESPERA - time.time()))
    _ultima[sitio] = time.time()
    _paginas[sitio] = _paginas.get(sitio, 0) + 1
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _abrir(req, timeout=30) as r:
            texto = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        destino = e.headers.get("Location", "") if 300 <= e.code < 400 else ""
        raise Bloqueado(f"HTTP {e.code} {destino}".strip()) from None
    except urllib.error.URLError as e:
        raise Bloqueado(f"error de red: {e.reason}") from None
    if any(m in texto for m in RETOS):
        raise Bloqueado("página de reto anti-bot")
    archivo.write_text(texto, encoding="utf-8")
    return texto, datetime.now().astimezone()


def obtener(sitio, url, parsear):
    """Patrón común: verifica el permiso del usuario, descarga y parsea; ante bloqueo o página sin datos regresa []."""
    if not habilitado(sitio):
        return []
    try:
        texto, descarga = descargar(sitio, url)
    except Bloqueado as e:
        bloqueado(sitio, url, str(e))
        return []
    vuelos = parsear(texto, descarga)
    if not vuelos:
        bloqueado(sitio, url, "la página no contiene vuelos")
    return vuelos


def filtrar(vuelos, desde, hasta, zona):
    """Deja los vuelos con salida_local en [desde, hasta]; datetime sin zona = hora local del aeropuerto."""
    a_local = lambda dt: dt.astimezone(zona).replace(tzinfo=None) if dt.tzinfo else dt
    d = a_local(desde).strftime("%Y-%m-%dT%H:%M") if desde else ""
    h = a_local(hasta).strftime("%Y-%m-%dT%H:%M") if hasta else "9999"
    return [v for v in vuelos if d <= v["salida_local"] <= h]
