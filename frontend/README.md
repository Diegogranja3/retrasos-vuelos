# Mapa de retrasos de vuelos (IAH)

Frontend en Vue 3 + Vite + Leaflet que muestra los vuelos programados que salen de IAH
en una franja de horas, como líneas de IAH a cada destino coloreadas por la probabilidad
de llegar con 15+ min de retraso (calculada por el modelo en el backend).

## Instalación y uso

Requiere Node 20.19+ o 22.12+.

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, usa el backend en http://localhost:8000 vía /api
npm run build      # genera dist/
```

## Modo de prueba (mock)

```bash
npm run mock       # = node scripts/generar_mock.mjs → public/mock/vuelos.json
npm run dev:mock   # vite --mode mock (carga .env.mock con VITE_MOCK=1)
```

El frontend usa `public/mock/vuelos.json` si `VITE_MOCK=1` o si `/api/vuelos` no responde
(error de red o 5xx sin JSON, que es lo que devuelve el proxy de Vite cuando el backend está apagado).
En ese caso aparece el distintivo «DATOS DE PRUEBA — no son predicciones reales».

El script toma el `.json` más reciente de `../data/raw/api/` (respuesta de AeroDataBox) o la ruta
que se le pase como argumento, y escribe solo los campos del contrato. Las probabilidades y el clima
son inventados con semilla fija. El mock contiene un solo día; se filtra solo por hora.

## Contrato del backend

`GET /api/vuelos?aeropuerto=IAH&desde=2026-10-29T06:00&hasta=2026-10-29T17:59` (horas locales, máx. 12 h)

```json
{
  "aeropuerto": {"iata": "IAH", "nombre": "...", "lat": 29.98, "lon": -95.34, "tz": "America/Chicago"},
  "generado": "2026-10-28T19:16:29Z",
  "fuente": "api | cache | mock",
  "modelo": {"nombre": "random_forest", "umbral": 0.32, "provisional": false},
  "vuelos": [{
    "id": "UA1234-2026-10-29", "numero": "UA 1234", "aerolinea": "UA", "aerolinea_nombre": "United",
    "destino": {"iata": "DEN", "nombre": "Denver", "lat": 39.86, "lon": -104.67},
    "salida_local": "2026-10-29T06:00", "salida_utc": "2026-10-29T11:00Z", "estado": "Expected",
    "probabilidad": 0.41, "prediccion": "retrasado | a_tiempo", "excluido": null,
    "contexto": {"origen": {"temperatura": 21.3, "precipitacion": 0.0, "rafagas": 30.1, "cape": 850,
                            "spc": "SLGT", "avisos_nws": []}, "destino": {"...": "..."}},
    "real": {"llegada_retraso_min": null, "retrasado": null}
  }]
}
```

- `probabilidad` y `prediccion` son `null` cuando `excluido` trae un motivo (texto).
- `real.retrasado` distinto de `null` → la interfaz muestra si la predicción acertó.
- Errores: HTTP 4xx/5xx con `{"detail": "mensaje"}`, que se muestra en la interfaz.
- Unidades supuestas en la interfaz: °C, mm, km/h y J/kg.

## Escala de color

Relativa al umbral `u` del modelo: verde `< 0.75·u`, amarillo `< u`, rojo `≥ u`; gris punteado para
excluidos (ocultos por defecto). Cada ruta toma el color de su vuelo con mayor probabilidad.

Teselas de OpenStreetMap (© OpenStreetMap contributors), sujetas a su política de uso.
