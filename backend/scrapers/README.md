# Scrapers de salidas de vuelos (IAH)

Fuente complementaria a la API de AeroDataBox para conocer los vuelos programados que **salen** de IAH.
Antes de escribir código se revisaron el `robots.txt` y los términos de uso de cada sitio candidato
(consulta: **2026-10-28**). Regla inicial: solo se programa scraper para sitios que lo permiten; nada de
rotar user-agents, proxies, navegadores headless ni saltar captchas/Cloudflare.

> **Excepción decidida por el usuario.** FlightAware, Flightradar24 y Airportia **prohíben el acceso automatizado en
> sus términos** (citas en la tabla de abajo). El usuario decidió usarlos de todos modos, asumiendo el riesgo, solo para
> este proyecto escolar sin fines de lucro. Sus scrapers están **desactivados por defecto** y solo corren con
> `SCRAPERS_SIN_PERMISO=1`; ver la sección "Sitios sin permiso". FlightStats queda fuera (captcha de AWS WAF).

## Sitios revisados

| Sitio | Clasificación | Evidencia (cita y URL) |
|---|---|---|
| **fly2houston.com** (Houston Airports, oficial) | **PROHIBIDO** (el endpoint de datos); el sitio en sí es DUDOSO | La página `/iah/flights/` no trae vuelos en el HTML: los pide por JS a `https://api.houstonairports.mobi/flights?scheduledTimestamp=A..B` (encabezados `Api-Key`, `Api-Version: 101`). El `robots.txt` de esa API dice `User-Agent: *` / `Disallow: /` (https://api.houstonairports.mobi/robots.txt). El sitio principal no tiene robots.txt (404, https://www.fly2houston.com/robots.txt) y sus términos dicen: "The Content on the Website is for your personal use only and not for commercial exploitation" y prohíben "Copy, modify, download, … reproduce, or distribute … material … protected by copyright … without obtaining permission" (https://www.fly2houston.com/airport-business/terms-and-conditions/). No prohíben bots expresamente, pero sin la API no hay datos que leer. |
| **FlightStats** (Cirium) | **PROHIBIDO** | robots.txt solo bloquea `/v2/airports/departing-arriving/search*` y cuentas (https://www.flightstats.com/robots.txt), pero los términos prohíben "Abstract, download, store, reproduce, transmit, display, copy or use the Cirium Data other than as expressly permitted" y usar los datos "in conjunction with … data sourced from any other similar flight data provider, or for any benchmarking purposes" (https://www.flightstats.com/company/legal/terms-of-use). Además usa captcha de AWS WAF. |
| **Flightradar24** | **PROHIBIDO** | "With the exception of the FR24 API, you may only access the Services via a human-operated web browser or mobile application, and not with any program, collection agent, or 'robot' … including … 'scraping' of data" (https://www.flightradar24.com/terms-and-conditions → /terms-of-service). |
| **FlightAware** (web) | **PROHIBIDO** | "you may only access the Website with a human-operated interactive web browser and not with any program, collection agent, or 'robot' for any purpose, including … 'scraping' of data" (https://www.flightaware.com/about/termsofuse). Su robots.txt también avisa: "automated fetching from the non-exportable data is not acceptable". |
| **Airportia** | **PROHIBIDO** | robots.txt bloquea `/api/` y `/flights/*/?day=` (https://www.airportia.com/robots.txt); términos: "Automated scraping, republication, or any commercial reuse of Airportia Data is forbidden" (https://www.airportia.com/about/). |
| **FAA** (nasstatus.faa.gov) | PERMITIDO pero **no útil** | robots.txt sin restricciones (https://nasstatus.faa.gov/robots.txt), pero solo publica el estado del aeropuerto (demoras de tierra, cierres), no vuelos individuales. |
| **Avionio** | **PERMITIDO** (implementado) | robots.txt: `User-agent: *` / `Disallow: /*?*` / `Allow: /` (https://www.avionio.com/robots.txt): permite `/en/airport/iah/departures` y `/en/flight/...`, pero **no** URLs con parámetros. No publica términos de uso (`/en/pages/terms` da 404); su única página legal es la de privacidad (https://www.avionio.com/en/pages/privacy), que no habla de acceso automatizado. |

Fuentes oficiales con resultado real, ya usadas en el proyecto: BTS On-Time Performance (`src/datos.py`) trae la llegada
real, pero con meses de retraso. OpenSky Network tiene API de investigación (no es scraping) con horas reales estimadas
por ADS-B pero sin número de vuelo comercial ni horario programado; no se evaluó aquí.

## Avionio: qué da y qué no

`scrapers/avionio.py` descarga **solo** `https://www.avionio.com/en/airport/iah/departures`. La página muestra unas
20 salidas operadas (~80 filas con códigos compartidos) cerca de la hora actual (en las pruebas: 20:00–20:08, consultando entre 20:30 y 20:45). Para ver otra hora
o la página siguiente el sitio usa `?ts=…&page=…`, que su robots.txt prohíbe; por eso **no se puede pedir una ventana
arbitraria**: `obtener_salidas(desde, hasta)` filtra lo que haya en la página y puede regresar `[]`.

| Campo normalizado | Avionio | Notas |
|---|---|---|
| `numero`, `aerolinea` | sí | del enlace `/en/flight/ua-1328`. Los regionales aparecen con el número del **operador** (`OO 4998`, `YV 6132`), mientras AeroDataBox/BTS usan el comercial (`UA 4998`, mismo número). |
| `aerolinea_nombre` | sí | |
| `destino_iata` | sí | |
| `salida_local` | sí | atributo `data-ts`: es la hora local escrita como si fuera UTC (no un instante real). |
| `salida_utc` | calculado | con `zoneinfo` (`America/Chicago` vía `airportsdata`), nunca con desfase fijo. |
| `estado` | sí | texto tal cual: `Departed 20:08`, `Estimated 21:15`, … |
| `es_codeshare`, `operador_iata`, `codeshares` | sí | la fila principal es el vuelo que opera; las filas `tt-child` son sus códigos compartidos (EK, NZ, AC, …). Se supone que el operador es la aerolínea del número principal. |
| `real.salida_real_local` | sí | solo de `Departed HH:MM` (hora local de IAH). `Estimated` no es real → `None`. |
| `real.llegada_programada_local` | **None** | no está en la tabla de salidas. |
| `real.llegada_real_local` | **None** | ver abajo. |

**¿Resultado real de llegadas?** La página de cada vuelo (`/en/flight/ua-5`, permitida) trae un historial de ~20 días
con `Landed HH:MM`, pero la hora no declara zona y no cuadra con ninguna zona fija: para UA 5 IAH→LHR (llegada programada
10:15–11:35 hora de Londres) aparece `Landed 05:43` el 27-oct y `Landed 09:11` el 23-oct; ni en hora de Houston, ni de
Londres, ni UTC ambos valores son plausibles. Como no se puede convertir sin inventar, **no se implementó
`obtener_resultados`**. Para evaluar predicción vs. realidad sigue siendo mejor BTS (con su retraso de publicación).
De cualquier forma, los datos de resultado real nunca deben usarse como variables del modelo, solo para evaluar.

## Buen comportamiento

- User-agent: `PIA-UANL-prediccion-retrasos/0.1 (proyecto escolar, uso academico no comercial)`.
- Consulta `robots.txt` antes de pedir una página y rechaza cualquier URL con `?`.
- Mínimo 3 s entre peticiones (Avionio no declara `Crawl-delay`), timeout de 30 s, 3 intentos con espera de 10/20 s
  solo ante 429/5xx o fallos de red (un 403/404 no se reintenta).
- Caché en `data/cache/scrapers/` con TTL de 1 h: una llamada normal hace como máximo 2 peticiones (robots + salidas).

## Sitios sin permiso (desactivados por defecto)

Módulos `flightradar24.py`, `airportia.py`, `flightaware.py`, con la descarga común en `sin_permiso.py`:

- Solo corren con `SCRAPERS_SIN_PERMISO=1`; sin eso avisan y regresan `[]`.
- Peticiones HTTP normales a la página pública de salidas, con el mismo user-agent fijo; sin sesión, proxies, headless,
  ni llaves/tokens de APIs internas (tampoco la `Api-Key` del JS de fly2houston).
- Mínimo **10 s** entre peticiones al mismo sitio, **máximo 3 páginas** por sitio por ejecución, caché de 1 h.
- No se siguen redirecciones. Si hay 403/429, redirección (p. ej. a un login), página de reto o la página no trae vuelos:
  se escribe "bloqueado" en `data/cache/scrapers/bloqueos.log`, se avisa con `warnings` y se regresa `[]` sin reintentar.

Resultado de la prueba en vivo (2026-10-28, máximo 2 peticiones por sitio):

| Sitio | Resultado | Campos |
|---|---|---|
| **Flightradar24** `/data/airports/iah/departures` | **Funciona**: 100 vuelos operados + 143 códigos compartidos, de las 21:20 a las 07:35 del día siguiente (el HTML ya trae la tabla; no se usa la API interna). | número, aerolínea y nombre, destino, hora programada (sin fecha: se deduce de la hora de descarga y del paso de medianoche), estado (`Estimated dep. 8:59 PM`, `Departed …`), códigos compartidos con nombre, `operador_iata` = aerolínea que FR24 muestra (United para United Express). Sin llegadas. |
| **Airportia** `/united-states/george-bush-intercontinental-houston-airport/departures/` | **Bloqueado**: la 1.ª petición (curl) trajo la tabla (19 operados, 66 filas, 20:30–22:14); la 2.ª, ya desde el scraper (urllib), respondió **HTTP 403**. No se intentó rodear. | si responde: fecha, hora programada, hora real de salida, estado, operador y códigos compartidos. Sin llegadas. |
| **FlightAware** `/live/airport/KIAH/departures` | **Bloqueado**: **HTTP 301 a `https://login.flightaware.com/signin`** las 2 veces; no iniciamos sesión. | ninguno; no hay parser porque nunca se vio la tabla. |

Comparación FR24 vs. AeroDataBox (`data/raw/api/aerodatabox_IAH_20261028_191629.json`), ventana común 2026-10-29
06:00–07:35, solo vuelos operados: **55 coinciden** por número y hora. Solo en FR24: 2 de UPS (`5X`), 1 de FedEx (`FX`) y
un privado (`N170XY`, que AeroDataBox parte mal como `N1 70XY`). Solo en AeroDataBox: ese mismo privado y 3 vuelos UA
de las 07:35, que quedaron fuera porque la página de FR24 corta en 100 filas.

## Uso

```bash
python -m scrapers.avionio                                   # imprime las salidas visibles ahora
python -m scrapers.avionio --comparar ../data/raw/api/aerodatabox_IAH_20261028_191629.json
python -m scrapers.test_scrapers                             # pruebas sin red (fragmento en fixtures/)
SCRAPERS_EN_VIVO=1 python -m scrapers.test_scrapers          # + prueba en vivo de Avionio (2 peticiones)
SCRAPERS_SIN_PERMISO=1 python -m scrapers.flightradar24      # sitios sin permiso (ver arriba)
```

```python
from datetime import datetime
from scrapers import obtener_salidas
vuelos = obtener_salidas("IAH", datetime(2026, 10, 28, 20, 0), datetime(2026, 10, 28, 21, 0))  # hora local de IAH
```

Dependencias: solo biblioteca estándar + `airportsdata` (ya en `requirements.txt`). No usa `requests` ni `beautifulsoup4`.

## Riesgos

- **Fragilidad:** el parseo es por expresiones regulares sobre clases CSS (`tt-row`, `tt-child`, `tt-f`, `tt-s`, …) y el
  atributo `data-ts`; cualquier rediseño de Avionio lo rompe. La prueba con `fixtures/` solo detecta cambios en el
  parseo, no en el sitio: corre la prueba en vivo de vez en cuando.
- **Cobertura:** ventana fija de la página por defecto; no sirve para pedir los vuelos de mañana. Avionio no tiene
  términos publicados, podrían aparecer después: revisa de nuevo antes de usarlo en serio.
- **Sitios sin permiso:** además del riesgo legal/de términos, pueden bloquear en cualquier momento (Airportia ya
  respondió 403); el HTML de FR24 usa clases de Tailwind (`text-l2-strong`, …) que cambian con cada rediseño.
- **Calidad:** Avionio agrega datos de terceros sin decir de dónde; el historial de aterrizajes ya mostró incoherencias.
