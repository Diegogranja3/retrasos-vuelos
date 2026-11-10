// Escala de riesgo, colores y formatos compartidos.

export const NIVELES = {
  // `tinta` es el color de texto sobre `color` (contraste ≥ 4.5:1).
  bajo: { color: '#1e7b34', tinta: '#fff', icono: '●', texto: 'Riesgo bajo' },
  medio: { color: '#e0a800', tinta: '#1a1a1a', icono: '◆', texto: 'Riesgo moderado' },
  alto: { color: '#c62828', tinta: '#fff', icono: '▲', texto: 'Riesgo alto' },
  excluido: { color: '#6b6b6b', tinta: '#fff', icono: '—', texto: 'Sin predicción' },
}

// Cortes absolutos para colorear. No se usa el umbral del modelo (~26 %): se eligió para maximizar F1 y queda
// por debajo de la tasa normal de retraso (~28 %), así que pintaba de rojo a casi cualquier vuelo.
export const CORTE_MEDIO = 0.2
export const CORTE_ALTO = 0.35

/** Nivel de riesgo para colores: bajo < 20 %, moderado 20–35 %, alto ≥ 35 %. */
export function nivelDe(probabilidad) {
  if (probabilidad == null) return 'excluido'
  if (probabilidad >= CORTE_ALTO) return 'alto'
  if (probabilidad >= CORTE_MEDIO) return 'medio'
  return 'bajo'
}

export function porcentaje(p) {
  return p == null ? '—' : `${Math.round(p * 100)} %`
}

/** "2026-10-29T06:05" -> "06:05" */
export function hora(salidaLocal) {
  return salidaLocal ? salidaLocal.slice(11, 16) : '—'
}

export function escaparHtml(texto) {
  return String(texto ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
}
