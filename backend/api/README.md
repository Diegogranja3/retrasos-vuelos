# Backend (FastAPI)

Predice si una salida programada de IAH llegará con 15+ min de retraso (ArrDel15), usando solo información
publicada al menos 24 h antes de la salida programada.

## Cómo correrlo

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000        # desde backend/
```

Prueba (sin gastar cuota; requiere `httpx`, que usa `fastapi.testclient`):

```bash
python -m api.test_api
```

## Variables de entorno

| Variable | Uso |
|---|---|
| `RAPIDAPI_KEY` | Llave de AeroDataBox (se lee de `.env`; nunca se imprime ni se guarda). |
| `MODELO_PATH` | Ruta del bundle (por omisión `models/modelo.joblib`). Se recarga solo si el archivo cambia. |
| `MODELO_PROVISIONAL=1` | Marca `modelo.provisional: true` en las respuestas. |
| `AERODATABOX_DESDE_ARCHIVO` | Usa un JSON guardado (p. ej. `data/raw/api/*.json`) en vez de llamar a la API. |
| `CACHE_DIR` | Raíz de la caché (por omisión `data/cache`). La prueba la apunta a un directorio temporal. |

## Contrato

- `GET /api/vuelos?aeropuerto=IAH&desde=2026-10-29T06:00&hasta=2026-10-29T17:59` — horas locales del aeropuerto.
  Ventana máxima de 12 h y `desde < hasta` (si no, 422). Solo `IAH` (otro aeropuerto, 400). Sin modelo, 503.
  Formato de la respuesta: el acordado con el frontend (`aeropuerto`, `generado`, `fuente`, `modelo`, `vuelos[]`).
- `GET /api/vuelos/{id}` — un vuelo, buscado en las predicciones guardadas (404 si no está).
- `GET /api/salud` — estado del modelo, unidades restantes de AeroDataBox (última cabecera
  `X-RateLimit-API-Units-Remaining` vista) y tamaño de la caché.
- Errores siempre como `{"detail": "..."}`. CORS abierto para `http://localhost:5173`.

## Decisiones

- **Mismas variables que en entrenamiento.** Cada vuelo pasa por `src.features.agregar_contexto` y luego por el
  pipeline guardado (`construir_variables` + `DuracionMediana`). `duracion_prog` se pasa como NaN: en vivo no existe
  y el pipeline la estima con medianas del entrenamiento. Del JSON de AeroDataBox solo se usa el horario
  programado; `estado` se muestra pero nunca entra al modelo.
- **Clima** (Open-Meteo Previous Runs API, siempre en GMT; en hora local aplica un desfase fijo sin horario de verano):
  para la hora t, si t ≤ ahora + 24 h se usa `*_previous_day1` (como en entrenamiento); si no, la corrida actual,
  que ya tiene ≥ 24 h de anticipación. Verificado el 2026-10-28: ambas variantes vienen completas para horas futuras;
  difieren hasta ~+15 h y después coinciden (la corrida "de 24 h antes" ya es la más reciente).
- **SPC Día 2 y avisos NWS** desde el IEM, igual que `src/datos.py`. `avisos_nws` lista los avisos que cumplen la
  misma regla que `src/features.py::_nws` (emitidos ≥ 24 h antes y vigentes a la salida). `spc` se traduce del rango
  numérico a TSTM/MRGL/SLGT/ENH/MDT/HIGH (`null` si no hay riesgo).
- **Población** (los vuelos fuera de ella se marcan en `excluido`, no se borran; `contexto` es `null` en ellos):
  - `destino sin coordenadas`: destino sin IATA o fuera de `airportsdata`.
  - `vuelo internacional`: `countryCode` fuera de {us, pr, vi, gu, as, mp}. BTS cuenta como nacionales los vuelos a
    territorios de EE. UU. (Puerto Rico, Islas Vírgenes, Guam, Samoa, Marianas), así que también aquí.
  - `codeshare duplicado`: `codeshareStatus == "IsCodeshared"`, o mismo destino + hora UTC + **terminal** que otro
    vuelo con número distinto (se conserva el `IsOperator`). La terminal se agregó porque en el JSON real hay vuelos
    distintos a la misma hora y destino desde terminales distintas (UA/F9 a CLT, AA/UA a DFW, UA/B6 a FLL).
  - `vuelo duplicado`: el proveedor repite el mismo número, destino y hora (14 casos en el JSON real).
  - `aerolínea no vista en entrenamiento`: sin IATA o fuera de `bundle["aerolineas"]`.
  - Un destino nacional que no esté en `bundle["destinos"]` **no** se excluye: el OneHotEncoder maneja categorías
    desconocidas.
- **`id`**: número sin espacios + fecha local (`UA1234-2026-10-29`); si se repite, se agrega `-2`, `-3`, ...
- **`real`** siempre en `null`: el resultado real (llegada) queda fuera de alcance por ahora para no gastar cuota.
- **`fuente`**: `api` si se descargó en esta petición; `cache` si vino de la caché (o de `AERODATABOX_DESDE_ARCHIVO`).

## Caché (`data/cache/`)

| Carpeta | Contenido | Vigencia |
|---|---|---|
| `api/` | Respuestas crudas de AeroDataBox (`IAH_desde_hasta.json`) y `cuota.json` | Reutiliza < 6 h; borra > 7 días (términos de AeroDataBox) |
| `contexto/` | Clima, SPC y NWS por aeropuerto | 1 h; si la descarga falla usa la copia vieja |
| `predicciones/` | Respuesta ya procesada (trabajo derivado) | Sin límite; se sirve si no hay red |

Sin red: se usa la caché cruda que cubra la ventana; si tampoco hay contexto, se sirve la última predicción guardada
que cubra la ventana (`fuente: "cache"`); si no hay nada, 503.

La primera consulta de una ventana tarda ~1 min (≈ 2 consultas al IEM por aeropuerto, ~70 aeropuertos);
las siguientes en la misma hora tardan ~2 s.
