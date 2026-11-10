// Acceso al backend (GET /api/vuelos) con respaldo a datos de prueba.

const URL_MOCK = `${import.meta.env.BASE_URL}mock/vuelos.json`

/**
 * Pide los vuelos del aeropuerto en la ventana [desde, hasta] (horas locales).
 * Devuelve { datos, esMock, motivoMock }.
 * Lanza Error con el `detail` del backend si este responde con un error propio.
 */
export async function obtenerVuelos({ aeropuerto, desde, hasta }) {
  if (import.meta.env.VITE_MOCK === '1') {
    return { datos: await cargarMock(desde, hasta), esMock: true, motivoMock: 'modo de prueba: VITE_MOCK=1' }
  }

  const params = new URLSearchParams({ aeropuerto, desde, hasta })
  let resp
  try {
    resp = await fetch(`/api/vuelos?${params}`, { headers: { Accept: 'application/json' } })
  } catch {
    return { datos: await cargarMock(desde, hasta), esMock: true, motivoMock: 'no se pudo conectar con el backend' }
  }

  let cuerpo = null
  try {
    cuerpo = await resp.json()
  } catch {
    cuerpo = null
  }

  if (!resp.ok) {
    // `fallo` ("vuelos" | "clima") dice qué consulta falló; App lo muestra en la notificación.
    if (cuerpo && typeof cuerpo.detail === 'string') throw Object.assign(new Error(cuerpo.detail), { fallo: cuerpo.fallo })
    // Un 5xx sin JSON suele venir del proxy de Vite cuando el backend no está corriendo.
    if (resp.status >= 500) {
      return { datos: await cargarMock(desde, hasta), esMock: true, motivoMock: 'el backend no responde' }
    }
    throw new Error(`Error HTTP ${resp.status}`)
  }
  if (!cuerpo || !Array.isArray(cuerpo.vuelos)) {
    throw new Error('El backend respondió con un formato inesperado.')
  }
  return { datos: cuerpo, esMock: cuerpo.fuente === 'mock', motivoMock: cuerpo.fuente === 'mock' ? 'el backend devolvió datos de prueba' : null }
}

// El mock contiene un solo día; se filtra por la hora del día de la ventana pedida.
async function cargarMock(desde, hasta) {
  const resp = await fetch(URL_MOCK)
  if (!resp.ok) throw new Error('No se pudieron cargar los datos de prueba (¿corriste scripts/generar_mock.mjs?).')
  const datos = await resp.json()
  const hDesde = desde.slice(11, 16)
  const hHasta = hasta.slice(11, 16)
  datos.vuelos = datos.vuelos.filter((v) => {
    const h = v.salida_local.slice(11, 16)
    // La ventana puede cruzar la medianoche (p. ej. 21:00 → 08:59).
    return hDesde <= hHasta ? h >= hDesde && h <= hHasta : h >= hDesde || h <= hHasta
  })
  return datos
}

/** GET/POST al backend; lanza Error con el `detail` del backend si responde con error. */
async function pedir(url, opciones) {
  const resp = await fetch(url, { headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, ...opciones })
  const cuerpo = await resp.json().catch(() => null)
  if (!resp.ok) {
    const d = cuerpo?.detail
    // FastAPI devuelve una lista de errores de validación (422).
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map((e) => `${e.loc?.slice(1).join('.')}: ${e.msg}`).join('; ') : `Error HTTP ${resp.status}`)
  }
  return cuerpo
}

/** Destinos, aerolíneas y orígenes que acepta el simulador. */
export const obtenerOpciones = () => pedir('/api/opciones')

/** Evalúa con el modelo un vuelo con condiciones escritas a mano. */
export const simularVuelo = (datos) => pedir('/api/simular', { method: 'POST', body: JSON.stringify(datos) })

/**
 * Explicación en lenguaje natural de la predicción (modelo de lenguaje gratuito vía OpenRouter).
 * Se recuerda la promesa por vuelo: App la pide en cuanto se elige el vuelo y el panel reutiliza esa misma
 * petición al aparecer, sin esperar ni repetirla.
 */
const explicaciones = new Map()
export function explicarVuelo(origen, vuelo) {
  const clave = `${origen}|${vuelo.id}`
  if (!explicaciones.has(clave)) {
    const p = pedir('/api/explicar', { method: 'POST', body: JSON.stringify({ origen, vuelo }) })
    p.catch(() => explicaciones.delete(clave)) // si falla, el siguiente intento vuelve a pedirla
    explicaciones.set(clave, p)
  }
  return explicaciones.get(clave)
}
