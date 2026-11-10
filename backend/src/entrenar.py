"""Fase 1: entrenamiento y evaluación.

    python -m src.entrenar

- Une vuelos + clima + riesgos (src.features.agregar_contexto) -> data/processed/dataset.parquet
- División temporal: entrenamiento < 2026-03-01 <= prueba. Validación = últimos 3 meses del entrenamiento.
- Hiperparámetros con TimeSeriesSplit dentro del entrenamiento; métrica de selección: PR-AUC (clase desbalanceada).
- Umbral de decisión elegido en validación (máximo F1), nunca en prueba.
- El mejor modelo se calibra (sigmoide/Platt) con la validación para que sus probabilidades sean interpretables;
  el modelo calibrado se entrena sin esos 3 meses (se usan para calibrar).
- Guarda el mejor pipeline (elegido por validación) en models/modelo.joblib y resultados en reports/.
"""
import json
import time
from pathlib import Path

import joblib
import sklearn
import matplotlib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV, CalibrationDisplay
from sklearn.compose import ColumnTransformer
from sklearn.frozen import FrozenEstimator
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (ConfusionMatrixDisplay, PrecisionRecallDisplay, RocCurveDisplay, average_precision_score,
                             brier_score_loss, confusion_matrix, f1_score, precision_recall_curve, precision_score, recall_score,
                             roc_auc_score)
from sklearn.model_selection import GridSearchCV, TimeSeriesSplit
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import FunctionTransformer, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.features import CAT, NUM, PROHIBIDAS, DuracionMediana, aeropuertos, agregar_contexto, construir_variables

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SEMILLA = 42
# ponytail: límites de memoria para equipos con ~3 GB libres; súbelos si sobra RAM.
N_JOBS = 2
sklearn.set_config(working_memory=256)
RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ.parent / "data"  # data/ vive fuera de backend/
PROC, REP, FIG = DATA / "processed", RAIZ / "reports", RAIZ / "reports" / "figuras"
INICIO_PRUEBA, INICIO_VAL = pd.Timestamp("2026-03-01"), pd.Timestamp("2025-12-01")
# Columnas de entrada del pipeline (lo que el backend debe proveer por vuelo).
ENTRADA = ["salida_local", "origen", "destino", "aerolinea", "duracion_prog"] + [
    c for c in NUM + CAT if c[:2] in ("o_", "d_")]


def pipeline(modelo, escalar=True):
    num = make_pipeline(SimpleImputer(strategy="median"), StandardScaler()) if escalar else SimpleImputer(strategy="median")
    return make_pipeline(
        FunctionTransformer(construir_variables),
        DuracionMediana(),
        ColumnTransformer([("cat", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=50), CAT),
                           ("num", num, NUM)]),
        modelo)


def submuestra(df, n):
    """Muestra sistemática que conserva el orden temporal (para TimeSeriesSplit)."""
    return df.iloc[:: max(1, len(df) // n)]


def metricas(y, prob, umbral):
    pred = (prob >= umbral).astype(int)
    return {"AUC": roc_auc_score(y, prob) if np.ptp(prob) else 0.5, "PR-AUC": average_precision_score(y, prob),
            "precision": precision_score(y, pred, zero_division=0), "recall": recall_score(y, pred),
            "F1": f1_score(y, pred), "umbral": umbral,
            "prob_media": float(np.mean(prob)), "tasa_real": float(np.mean(y)), "brier": brier_score_loss(y, prob)}


def umbral_f1(y, prob):
    p, r, t = precision_recall_curve(y, prob)
    f1 = 2 * p * r / np.clip(p + r, 1e-9, None)
    return float(t[np.argmax(f1[:-1])])


def cargar():
    ruta = PROC / "dataset.parquet"
    if ruta.exists():
        return pd.read_parquet(ruta)
    vuelos = pd.read_parquet(PROC / "vuelos.parquet")
    # Igual que el backend: sin coordenadas no hay distancia ni clima (p. ej. DJT, 10 vuelos).
    db = aeropuertos()
    sin_coord = ~vuelos["destino"].isin(db.keys())
    print(f"Vuelos a destinos sin coordenadas (se quitan): {sin_coord.sum()} {sorted(vuelos.loc[sin_coord, 'destino'].unique())}")
    vuelos = vuelos[~sin_coord]
    df = agregar_contexto(vuelos, pd.read_parquet(PROC / "clima.parquet"),
                          pd.read_parquet(PROC / "spc.parquet"), pd.read_parquet(PROC / "nws.parquet"))
    df = df.sort_values("salida_local").reset_index(drop=True)
    df.to_parquet(ruta, index=False)
    return df


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    df = cargar()
    assert not PROHIBIDAS & set(ENTRADA), "variable prohibida en la entrada"
    print(f"Dataset: {len(df)} vuelos, {df['salida_local'].min():%Y-%m} a {df['salida_local'].max():%Y-%m}; "
          f"filas con clima nulo: {df[[c for c in NUM if c.startswith(('o_t', 'd_t'))]].isna().any(axis=1).sum()}")
    print("Avisos NWS que cuentan (vuelos con >=1, origen):",
          {c: int((df[c] > 0).sum()) for c in NUM if c.startswith("o_nws")},
          "| SPC origen > 0:", int((df["o_spc"] > 0).sum()))

    tr, te = df[df["salida_local"] < INICIO_PRUEBA], df[df["salida_local"] >= INICIO_PRUEBA]
    sub_tr, val = tr[tr["salida_local"] < INICIO_VAL], tr[tr["salida_local"] >= INICIO_VAL]
    X, y = lambda d: d[ENTRADA], lambda d: d["ArrDel15"]
    print(f"Entrenamiento {len(tr)} (retraso {y(tr).mean():.3f}) | validación {len(val)} | "
          f"prueba {len(te)} (retraso {y(te).mean():.3f})")

    cv = TimeSeriesSplit(n_splits=3)
    candidatos = {
        "regresion_logistica": (pipeline(LogisticRegression(max_iter=3000, random_state=SEMILLA)),
                                {"logisticregression__C": [0.1, 1.0],
                                 "logisticregression__class_weight": [None, "balanced"]}, None),
        # ponytail: kNN escala O(n²); se busca k con 30k y el modelo final usa 60k vuelos (muestra sistemática).
        "knn": (pipeline(KNeighborsClassifier(n_jobs=N_JOBS)),
                {"kneighborsclassifier__n_neighbors": [25, 75, 151, 301],
                 "kneighborsclassifier__weights": ["uniform", "distance"]}, (30_000, 60_000)),
        # ponytail: búsqueda en 120k vuelos y cada árbol ve 50 % (max_samples) para que quepa en RAM/tiempo con IAH+DFW.
        "random_forest": (pipeline(RandomForestClassifier(n_jobs=N_JOBS, random_state=SEMILLA, min_samples_leaf=20, max_samples=0.5), escalar=False),
                          {"randomforestclassifier__n_estimators": [150, 300],
                           "randomforestclassifier__max_depth": [12, 20],
                           "randomforestclassifier__class_weight": [None, "balanced_subsample"]}, (120_000, None)),
    }

    resultados, modelos = {"siempre_sin_retraso": {"validacion": metricas(y(val), np.zeros(len(val)), 0.5),
                                                   "prueba": metricas(y(te), np.zeros(len(te)), 0.5)}}, {}
    for nombre, (pipe, grid, tam) in candidatos.items():
        t0 = time.time()
        busq = sub_tr if tam is None else submuestra(sub_tr, tam[0])
        gs = GridSearchCV(pipe, grid, cv=cv, scoring="average_precision", n_jobs=1).fit(X(busq), y(busq))
        print(f"\n[{nombre}] mejores parámetros (CV, PR-AUC {gs.best_score_:.3f}): {gs.best_params_}")
        # Umbral: se ajusta con datos previos a la validación y se elige en validación.
        final_tr = sub_tr if tam is None or tam[1] is None else submuestra(sub_tr, tam[1])
        m_val = pipe.set_params(**gs.best_params_).fit(X(final_tr), y(final_tr))
        p_val = m_val.predict_proba(X(val))[:, 1]
        umbral = umbral_f1(y(val), p_val)
        # Modelo final: todo el entrenamiento (o su submuestra para kNN).
        todo_tr = tr if tam is None or tam[1] is None else submuestra(tr, tam[1])
        modelo = pipe.set_params(**gs.best_params_).fit(X(todo_tr), y(todo_tr))
        p_te = modelo.predict_proba(X(te))[:, 1]
        resultados[nombre] = {"parametros": {k.split("__")[1]: v for k, v in gs.best_params_.items()},
                              "cv_pr_auc": gs.best_score_, "validacion": metricas(y(val), p_val, umbral),
                              "prueba": metricas(y(te), p_te, umbral)}
        modelos[nombre] = (modelo, p_te, umbral, m_val)
        print(f"  validación: {resultados[nombre]['validacion']}\n  prueba:     {resultados[nombre]['prueba']}"
              f"\n  ({time.time() - t0:.0f} s)")

    # Selección por validación (no por prueba).
    mejor = max(modelos, key=lambda k: resultados[k]["validacion"]["PR-AUC"])
    _, p_te_sin, _, m_val = modelos[mejor]
    print(f"\nMejor modelo (PR-AUC en validación): {mejor}")

    # Calibración sigmoide con la validación (el modelo base no vio esos meses). No cambia el orden de los vuelos
    # (AUC igual), solo la escala: class_weight="balanced" infla las probabilidades.
    modelo = CalibratedClassifierCV(FrozenEstimator(m_val), method="sigmoid").fit(X(val), y(val))
    p_val = modelo.predict_proba(X(val))[:, 1]
    umbral = umbral_f1(y(val), p_val)
    p_te = modelo.predict_proba(X(te))[:, 1]
    mejor = f"{mejor}_calibrada"
    resultados[mejor] = {"validacion": metricas(y(val), p_val, umbral), "prueba": metricas(y(te), p_te, umbral)}
    print(f"[{mejor}] prueba: {resultados[mejor]['prueba']}")
    CalibrationDisplay.from_predictions(y(te), p_te_sin, n_bins=10, name="sin calibrar")
    CalibrationDisplay.from_predictions(y(te), p_te, n_bins=10, name="calibrada", ax=plt.gca())
    plt.title("Calibración en prueba")
    plt.savefig(FIG / "calibracion.png", dpi=120)

    tabla = pd.DataFrame({k: v["prueba"] for k, v in resultados.items()}).T.round(3)
    print("\nPrueba (mar-ago 2026):\n" + tabla.to_string())

    # Figuras: ROC y PR de todos, matriz de confusión del mejor.
    fig, ax = plt.subplots(1, 2, figsize=(12, 5))
    for k, (_, p, _, _) in modelos.items():
        RocCurveDisplay.from_predictions(y(te), p, name=k, ax=ax[0])
        PrecisionRecallDisplay.from_predictions(y(te), p, name=k, ax=ax[1])
    ax[1].axhline(y(te).mean(), ls="--", c="gray", label="siempre sin retraso")
    ax[1].legend()
    fig.tight_layout()
    fig.savefig(FIG / "roc_pr.png", dpi=120)
    cm = confusion_matrix(y(te), (p_te >= umbral).astype(int))
    ConfusionMatrixDisplay(cm, display_labels=["a tiempo", "retrasado"]).plot(cmap="Blues")
    plt.title(f"{mejor} (umbral {umbral:.2f})")
    plt.savefig(FIG / "matriz_confusion.png", dpi=120)

    # Importancia por permutación sobre las columnas de entrada (interpretables).
    muestra = te.sample(min(15_000, len(te)), random_state=SEMILLA)
    # salida_local agrupa todo el calendario (mes, día, hora); duracion_prog no se usa al predecir.
    imp = permutation_importance(modelo, X(muestra), y(muestra), scoring="average_precision",
                                 n_repeats=3, random_state=SEMILLA, n_jobs=1)
    imp = pd.Series(imp.importances_mean, index=ENTRADA).drop("duracion_prog").sort_values(ascending=False)
    imp.head(20)[::-1].plot.barh(figsize=(8, 7), title=f"Importancia por permutación ({mejor}, caída de PR-AUC)")
    plt.tight_layout()
    plt.savefig(FIG / "importancia.png", dpi=120)

    # Errores por aerolínea y por destino (los 15 con más vuelos).
    te = te.assign(prob=p_te, pred=(p_te >= umbral).astype(int))

    def por(col):
        g = te.groupby(col)
        t = pd.DataFrame({"vuelos": g.size(), "tasa_real": g["ArrDel15"].mean(), "prob_media": g["prob"].mean(),
                          "recall": g.apply(lambda d: recall_score(d["ArrDel15"], d["pred"], zero_division=0)),
                          "AUC": g.apply(lambda d: roc_auc_score(d["ArrDel15"], d["prob"]) if d["ArrDel15"].nunique() > 1 else np.nan)})
        return t.sort_values("vuelos", ascending=False).head(15).round(3)

    errores = {"origen": por("origen"), "aerolinea": por("aerolinea"), "destino": por("destino")}
    for k, t in errores.items():
        print(f"\nErrores por {k}:\n{t.to_string()}")

    (RAIZ / "models").mkdir(exist_ok=True)
    joblib.dump({"modelo": modelo, "nombre": mejor, "umbral": umbral, "entrada": ENTRADA,
                 "entrenado_hasta": str(INICIO_PRUEBA.date()), "aerolineas": sorted(tr["aerolinea"].unique()),
                 "origenes": sorted(tr["origen"].unique()),
                 "destinos": sorted(tr["destino"].unique())}, RAIZ / "models" / "modelo.joblib")
    (REP / "metricas_fase1.json").write_text(json.dumps(
        {"resultados": resultados, "mejor": mejor, "importancia": imp.round(5).to_dict(),
         "errores": {k: t.reset_index().to_dict("records") for k, t in errores.items()},
         "matriz_confusion": cm.tolist()}, indent=2, default=float), encoding="utf-8")
    print("\nGuardado: models/modelo.joblib, reports/metricas_fase1.json, reports/figuras/")


if __name__ == "__main__":
    main()
