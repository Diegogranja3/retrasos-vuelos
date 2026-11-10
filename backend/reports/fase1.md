# Fase 1 — Datos y entrenamiento (IAH + DFW)

Reproducir: `python -m src.datos bts`, `python -m src.datos clima`, `python -m src.datos riesgos`, `python -m src.entrenar`.
Resultados crudos: `reports/metricas_fase1.json`; figuras en `reports/figuras/` (ROC/PR, matriz de confusión,
importancia y calibración). Prueba de variables: `python -m tests.test_features`.

## Datos

- **Vuelos:** BTS *Marketing Carrier On-Time Performance*, salidas de **IAH y DFW**, sep-2024 a ago-2026.
  971,965 → 948,530 vuelos tras quitar cancelados y desviados (23,350), 1 duplicado y 84 vuelos a DJT, que no tiene
  coordenadas. La aerolínea es la **comercializadora**, la misma que da la API en vivo.
- **Clima:** Open-Meteo Previous Runs, `*_previous_day1` (pronóstico emitido 24 h antes), en UTC, para 197 aeropuertos,
  0 % de nulos. Variables: temperatura, precipitación, nieve, nubosidad, viento, ráfagas, CAPE y `weather_code`, en
  origen y destino.
- **Riesgos (IEM):**
  - Panorama convectivo del SPC, Día 2: 116,016 panoramas.
  - Vigilancias y avisos del NWS: 48,792 eventos.
  - Solo cuentan los **emitidos ≥ 24 h antes** de la salida.
- **Regla de anticipación:** todo lo externo se publicó al menos 24 h antes. Las variables prohibidas se verifican
  automáticamente (`PROHIBIDAS` en `src/features.py`).

## Variables (`src/features.py`, idénticas en entrenamiento e inferencia)

- **Vuelo:** aeropuerto de origen, aerolínea, destino y distancia de gran círculo calculada con las coordenadas.
- **Calendario:** mes, día del mes, día de la semana y hora local de salida.
- **Duración estimada:** mediana del entrenamiento por (aerolínea, ruta), luego por ruta y luego global. Se calcula
  dentro del pipeline, así que no usa datos de prueba.
- **Clima y riesgos:** en origen y destino. El riesgo SPC va como ordinal, y los avisos NWS se cuentan por grupo.

## Método

- **División temporal:**

  | Conjunto | Periodo | Vuelos | Tasa de retraso |
  |---|---|---|---|
  | Entrenamiento | sep-2024 a feb-2026 | 703,848 | 23.7 % |
  | Validación | últimos 3 meses del entrenamiento (dic-2025 a feb-2026) | 109,294 | — |
  | Prueba | mar a ago-2026 | 244,682 | 26.8 % |

- **Hiperparámetros:** `GridSearchCV` con `TimeSeriesSplit(3)` dentro del entrenamiento, eligiendo por PR-AUC.
- **Selección:** el mejor modelo por PR-AUC **en validación**.
- **Calibración:** el modelo elegido se calibra con una sigmoide (Platt) usando la validación. Hace falta porque
  `class_weight="balanced"` inflaba las probabilidades (media 54.5 % contra 26.8 % real). La calibración no cambia
  el orden de los vuelos, así que el AUC queda igual. El modelo calibrado se entrena sin los 3 meses de validación,
  que se usan para calibrar.
- **Umbral:** el que maximiza F1 en validación, sobre las probabilidades calibradas. Nunca se tocó la prueba.
- **Submuestras, por memoria y tiempo:**
  - kNN: búsqueda con 30k vuelos y modelo final con 60k.
  - Random Forest: búsqueda con 120k; cada árbol ve el 50 % del entrenamiento.
- **Semilla:** 42.

| Modelo | Mejores parámetros |
|---|---|
| Regresión logística | C = 0.1, `class_weight` = balanced |
| kNN (escalado) | k = 301, pesos por distancia |
| Random Forest | 150 árboles, `max_depth` = 12, `min_samples_leaf` = 20, `max_samples` = 0.5 |

## Resultados en prueba (mar–ago 2026)

| Modelo | AUC | PR-AUC | Precision | Recall | F1 | Prob. media | Brier |
|---|---|---|---|---|---|---|---|
| Siempre sin retraso | 0.500 | 0.268 | 0.000 | 0.000 | 0.000 | 0.000 | 0.268 |
| Regresión logística (sin calibrar) | 0.683 | 0.442 | 0.319 | 0.869 | 0.467 | 0.545 | 0.258 |
| kNN | 0.621 | 0.375 | 0.285 | 0.935 | 0.437 | 0.268 | 0.189 |
| Random Forest | 0.658 | 0.407 | 0.289 | 0.939 | 0.441 | 0.276 | 0.186 |
| **Regresión logística calibrada** | **0.683** | **0.442** | **0.330** | 0.833 | **0.473** | **0.300** | **0.181** |

Tasa real de retraso en prueba: 26.8 %.

- **Modelo en producción:** regresión logística calibrada (`models/modelo.joblib`, umbral 0.23). Es el mejor en
  AUC, PR-AUC, F1 y Brier.
- **Sin señales de fuga:** el AUC de 0.68 está dentro de lo esperado (0.65–0.70).
- **Calibración:** la probabilidad media pasó de 54.5 % a 30.0 %, contra 26.8 % real, y el Brier bajó de 0.258 a 0.181.
  Ver `figuras/calibracion.png`.
- **Matriz de confusión** (umbral 0.23):

  |  | Predijo a tiempo | Predijo retraso |
  |---|---|---|
  | Real a tiempo | 68,317 | 110,768 |
  | Real retrasado | 10,952 | 54,645 |

  Detecta el 83 % de los retrasos con precisión del 33 %. El umbral que maximiza F1 es bajo, así que la etiqueta
  "retrasado" es generosa; en la demo conviene leer la **probabilidad**.

## Importancia de variables (permutación, caída de PR-AUC)

| Variable | Importancia |
|---|---|
| Calendario (hora, mes, día) | 0.117 |
| Destino | 0.029 |
| Temperatura pronosticada en origen | 0.025 |
| Aerolínea | 0.024 |
| Riesgo SPC Día 2 en origen | 0.021 |
| Código de tiempo (WMO) en origen | 0.013 |
| Avisos de inundación en origen | 0.009 |

Solo en IAH, la tasa de retraso pasa de 18.8 % sin riesgo SPC a 34.1 % con riesgo marginal y a 62.3 % con riesgo ligero.

## Errores por aerolínea (modelo calibrado)

| Aerolínea | Vuelos en prueba | Tasa real | Prob. media | Recall | AUC |
|---|---|---|---|---|---|
| AA | 144,772 | 30.0 % | 32.7 % | 0.89 | 0.68 |
| UA | 76,159 | 21.0 % | 25.1 % | 0.71 | 0.67 |
| DL | 9,970 | 20.2 % | 22.6 % | 0.63 | 0.68 |
| F9 | 9,280 | 29.3 % | 35.6 % | 0.91 | 0.64 |
| NK | 1,575 | 37.2 % | 22.1 % | 0.51 | 0.59 |
| B6 | 906 | 43.2 % | 41.7 % | 0.96 | 0.64 |

**Spirit (NK) sigue subestimada:** su puntualidad empeoró en 2026, un cambio de distribución que el modelo no ve.
El detalle por destino está en `metricas_fase1.json`.

## Limitaciones

- **Avisos NWS con poco efecto:** casi ningún aviso se emite ≥ 24 h antes, y no hubo huracanes en IAH ni en DFW
  durante el periodo.
- **Retrasos que vienen del día del vuelo** (avión que llega tarde, saturación del espacio aéreo) quedan fuera por diseño.
- **kNN es el peor modelo** y el más lento; la escala O(n²) obligó a submuestrear.
- **El modelo calibrado no vio los últimos 3 meses de entrenamiento**, porque se usan para calibrar.
- **Desfase en vivo:** en vivo, para vuelos a más de 24 h se usa el pronóstico actual, no el `_previous_day1`; su
  anticipación es igual o mayor que la del entrenamiento.
