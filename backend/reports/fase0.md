# Fase 0 — Validación de viabilidad

Fecha: 2026-10-28 · Aeropuerto: IAH (Houston) · Mes de prueba: marzo 2026 (incluye el cambio de horario del 8 de marzo)

## 1. API de vuelos (AeroDataBox vía RapidAPI)

Script: `scripts/check_api.py`. Una llamada a `GET /flights/airports/iata/IAH/{desde}/{hasta}` (ventana de 12 h de vuelos
programados del día siguiente). Respuesta cruda guardada en `data/raw/api/`.

**Esquema observado:** `{"departures": [...], "arrivals": [...]}` (454 salidas y 437 llegadas en 12 h). Por vuelo:
`number`, `callSign`, `status`, `codeshareStatus`, `isCargo`, `airline{iata,icao,name}`, `aircraft{model,reg,modeS}`,
`movement{airport{iata,icao,name,timeZone,countryCode,location{lat,lon}}, scheduledTime{local,utc}, revisedTime, runwayTime, terminal, quality}`.
En `departures`, `movement.airport` es el **destino** y `movement.scheduledTime` es la **salida desde IAH**.

| Campo necesario | Ruta en la respuesta | Presente (30 vuelos) |
|---|---|---|
| Aerolínea (IATA) | `airline.iata` | 29/30 |
| Origen | *(no viene: es el aeropuerto consultado)* | implícito |
| Destino | `movement.airport.iata` | 30/30 |
| Horario programado | `movement.scheduledTime.local` / `.utc` | 30/30 |
| Zona horaria origen | *(solo el desfase en `scheduledTime.local`, p. ej. `-05:00`)* | 30/30 |
| Zona horaria destino | `movement.airport.timeZone` (IANA) | 30/30 |
| Distancia | **no existe** (derivable de `location.lat/lon`) | — |
| Duración programada | **no existe** en salidas | — |

**Problemas encontrados (de las 454 salidas):**

- **La aerolínea que da la API es la comercializadora, no la operadora.** Los vuelos regionales salen como `UA` aunque los
  operen SkyWest (`SKW`), Mesa (`ASH`), CommuteAir (`UCA`) o GoJet (`GJS`), según el `callSign`. El dataset que usé
  (*Reporting Carrier*) etiqueta por operador (`OO`, …). Además, ese dataset **no incluye** a CommuteAir (C5) ni a Mesa (YV):
  en marzo de 2026 faltan unos 4,400 de los ~14,900 vuelos de IAH (~29 %).
- `codeshareStatus`: 250 `IsOperator`, 4 `IsCodeshared`, 200 `Unknown`. Los `Unknown` no traen `callSign`, pero solo 1 de
  200 coincide en destino y hora con un `IsOperator`. Es decir, en su mayoría son vuelos reales y no duplicados de
  codeshare, aunque de ellos no se puede saber el operador.
- La respuesta incluye vuelos internacionales: 26 de los `IsOperator` van a México. Se filtran con `countryCode == "us"`.

**Cuota:** cada llamada costó **2 unidades** (cabecera `X-RateLimit-API-Units-Remaining`: 400 → 398).

## 2. Límite del plan gratuito

Según [aerodatabox.com/pricing](https://aerodatabox.com/pricing/) (consultado el 2026-10-28):

- Plan Basic: **400 unidades al mes**, 1 solicitud por segundo; "Free forever" en RapidAPI.
- Las unidades no son llamadas: los endpoints de Tier 1, 2 y 3 cuestan 1, 2 y 6 unidades.
- Las cabeceras reales confirman `X-RateLimit-API-Units-Limit: 400`.
- Con 2 unidades por ventana de 12 h, alcanza para unas **200 consultas al mes**: sobra para la demo.
- **Restricción de caché:** según su FAQ, las respuestas crudas se pueden guardar **hasta 7 días**, salvo que sean trabajo
  derivado. Para presentar sin conexión hay que refrescar la caché la semana de la presentación o guardar solo el
  resultado ya procesado (vuelo + predicción).

## 3. Cruce con clima (Open-Meteo)

Script: `scripts/check_weather_join.py` → `data/interim/IAH_2026_03.parquet`.

- **Fuente del clima:** la *Historical Forecast API* pega las **primeras horas** de cada corrida del modelo, así que equivale
  prácticamente a clima observado ([docs](https://open-meteo.com/en/docs/historical-forecast-api)). Para el escenario
  "con anticipación" usé la **Previous Runs API**, con variables `*_previous_day1` (el valor pronosticado 24 h antes, con datos
  desde enero de 2024; [docs](https://open-meteo.com/en/docs/previous-runs-api)). En la demo se usa el pronóstico vigente.
- Variables: temperatura, precipitación, nieve, nubosidad, viento, ráfagas y `weather_code`; 0 % de nulos.
  `visibility_previous_day1` viene 100 % vacía y la descarté.
- **Filas:** 10,492 salidas → 10,147 sin cancelados ni desviados → 10,147 tras el cruce, con 0 nulos de clima.
  Semana del 5 al 11 de marzo: 2,273 de 2,273 vuelos con clima completo.
- 94 aeropuertos y todos tienen coordenadas y zona IANA (`airportsdata`). 0 horas locales inexistentes o ambiguas el día
  del cambio de horario.
- **Error de zona horaria detectado y evitado:** si a Open-Meteo se le pide el clima en "hora local", aplica **un solo desfase
  fijo** (`utc_offset_seconds`, el actual) a todo el rango. Unir por hora local habría desalineado una hora todos los
  vuelos de invierno: 2,246 diferencias en horario de invierno contra 0 en horario de verano. **Solución:** pedir el clima en UTC y convertir cada
  vuelo de hora local a UTC con la zona IANA de su aeropuerto. Con eso, el desfase cambia correctamente de −6 a −5 el 8 de marzo.
- Otro detalle corregido: `dt.round("h")` de pandas redondea los :30 al par más cercano, lo que desalinea hora local contra
  UTC. Ahora se redondea hacia arriba.

## 4. Baseline

Script: `scripts/baseline.py`.

- División temporal: días 1–23 para entrenar (7,485 vuelos, 25.8 % retrasados) y días 24–31 para probar (2,662 vuelos, 17.4 %).
- Variables: día de la semana, hora de salida, aerolínea, destino, distancia, duración programada y clima en origen y destino.
- Modelo: regresión logística con `class_weight="balanced"`, umbral 0.5.

| Modelo | AUC | PR-AUC | Precision | Recall | F1 |
|---|---|---|---|---|---|
| Siempre sin retraso | 0.500 | 0.174 | 0.000 | 0.000 | 0.000 |
| Logística sin clima | 0.628 | 0.275 | 0.236 | 0.559 | 0.332 |
| Logística con clima | 0.629 | 0.273 | 0.266 | 0.330 | 0.295 |

Un AUC de ~0.63 está en el rango esperado y no hay señales de fuga de datos. Con un solo mes, el clima casi no aporta:
23 días de entrenamiento traen muy poca variedad de eventos climáticos. Hay que volver a medirlo con 1–2 años de datos.
Con un mes, las métricas son ruidosas.

## Recomendación

**Continuar** con la API en vivo (plan A), con estos cambios que necesito que apruebes:

1. **Cambiar al dataset *Marketing Carrier On-Time Performance* (BTS, 2018 en adelante)**, que trae aerolínea comercializadora
   y operadora (`IATA_Code_Marketing_Airline`, `IATA_Code_Operating_Airline`). La variable "aerolínea" sería la
   comercializadora, igual que `airline.iata` en la API. Además recupera el ~29 % de vuelos de IAH que faltan (C5, YV).
   La alternativa, usar la operadora, no funciona en vivo: en ~45 % de las salidas la API no trae `callSign`.
2. **Duración programada:** la API no la da. Opciones: (a) quitarla de las variables; (b) usar la mediana histórica por ruta y
   aerolínea, calculada con los datos de entrenamiento; (c) consultar también las llegadas en el destino, lo que gasta más
   cuota. Recomiendo (b) o (a).
3. **Distancia:** la API no la da. Propongo calcularla con la fórmula de gran círculo usando las coordenadas, en entrenamiento
   y en inferencia, en lugar de usar la columna `Distance` de BTS. Así se garantiza que sea la misma variable en los dos lados.
4. **Origen:** se toma del aeropuerto consultado, y **la zona horaria del origen** de `airportsdata` (IAH = America/Chicago).
