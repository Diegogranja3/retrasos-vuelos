<script setup>
// Mapa Mapbox GL: origen, un punto por destino y una línea curva por vuelo (del color de su riesgo).
// Al elegir un vuelo: vista lejana e inclinada de toda la ruta y un avión 3D que la recorre hasta el destino.
// Token y estilo vienen del .env de la raíz (VITE_MAPBOX_TOKEN, VITE_MAPBOX_STYLE).
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import mapboxgl from 'mapbox-gl'
import { NIVELES, hora, porcentaje } from '../util.js'

const props = defineProps({
  aeropuerto: { type: Object, required: true },
  vuelos: { type: Array, required: true }, // con `nivel` ya calculado, igual que en el listado
  vuelo: { type: Object, default: null }, // vuelo elegido (se anima)
})
const emit = defineEmits(['seleccionar-vuelo'])

const { VITE_MAPBOX_TOKEN: TOKEN, VITE_MAPBOX_STYLE: ESTILO } = import.meta.env
const sinToken = !TOKEN || !ESTILO
const contenedor = ref(null)
let mapa = null
let marcadorOrigen = null
let observador = null
let listo = false
const etiqueta = new mapboxgl.Popup({ closeButton: false, closeOnClick: false, offset: 8, className: 'popup-vidrio' })

// ---------- Avión 3D ----------
const MODELO = '/modelos/boeing747.glb' // Boeing 747 de Miha Lunar (CC-BY), poly.pizza/m/49CLof4tP2V
const LARGO_MODELO = 1.5 // unidades del glTF de punta a cola
const PX_AVION = 130 // tamaño aproximado del avión en pantalla, a cualquier zoom
const ALTURA_PX = 45 // altura de crucero en pantalla
// ponytail: calibrado a ojo para este glTF (vista cenital y lateral). Su nariz va sobre el eje Y con 15° de desfase,
// y orientation = [x: + nariz abajo, y: + ala izquierda abajo, z: rumbo]. Si se cambia de modelo, recalibrar.
const GIRO_MODELO = -15
// Escala para que el avión mida ~PX_AVION px: a zoom 14 (≈4 m/px) son ~220 unidades; se duplica por cada zoom menos.
const ESCALA_Z14 = (PX_AVION * 4.1) / LARGO_MODELO
const ID_AVION = { source: 'avion', sourceLayer: '', id: 'avion' }
const reducirMovimiento = window.matchMedia('(prefers-reduced-motion: reduce)').matches
let animacion = null // id de requestAnimationFrame

// Arco simple (Bézier cuadrática) entre dos puntos [lat, lon]; devuelve coordenadas GeoJSON [lon, lat].
function arco(origen, destino, pasos = 32, curva = 0.2) {
  const [la1, lo1] = origen
  let [la2, lo2] = destino
  // Toma el camino corto en longitud (p. ej. hacia Asia por el Pacífico).
  if (lo2 - lo1 > 180) lo2 -= 360
  if (lo2 - lo1 < -180) lo2 += 360
  const dLa = la2 - la1
  const dLo = lo2 - lo1
  // Punto de control desplazado en perpendicular (por omisión, al 20 % de la distancia).
  const cLa = (la1 + la2) / 2 + dLo * curva
  const cLo = (lo1 + lo2) / 2 - dLa * curva
  const puntos = []
  for (let i = 0; i <= pasos; i++) {
    const t = i / pasos
    const u = 1 - t
    puntos.push([u * u * lo1 + 2 * u * t * cLo + t * t * lo2, u * u * la1 + 2 * u * t * cLa + t * t * la2])
  }
  return puntos
}

// Vuelos a dibujar: los del listado más el elegido si no está en él (un vuelo simulado).
const dibujables = () =>
  props.vuelo && !props.vuelos.some((v) => v.id === props.vuelo.id) ? [...props.vuelos, props.vuelo] : props.vuelos

/**
 * Curvatura de cada vuelo: los que van al mismo destino se abren en abanico (ordenados por hora) para que cada
 * uno tenga su propia raya. El avión recorre exactamente esa curva.
 */
function curvaturas() {
  const porDestino = new Map()
  for (const v of dibujables()) {
    const d = v.destino.iata ?? v.destino.nombre
    if (!porDestino.has(d)) porDestino.set(d, [])
    porDestino.get(d).push(v)
  }
  const curva = new Map()
  for (const grupo of porDestino.values()) {
    grupo.sort((a, b) => a.salida_local.localeCompare(b.salida_local))
    const paso = Math.min(0.05, 0.3 / grupo.length)
    grupo.forEach((v, k) => curva.set(v.id, 0.2 + (k - (grupo.length - 1) / 2) * paso))
  }
  return curva
}

// GeoJSON de vuelos y destinos; el estilo (color, grosor, opacidad) va como propiedades de cada elemento.
function geojson() {
  const origen = [props.aeropuerto.lat, props.aeropuerto.lon]
  const elegido = props.vuelo?.id
  const curva = curvaturas()
  const lineas = []
  const destinos = new Map()
  for (const v of dibujables()) {
    const sel = v.id === elegido
    const coords = arco(origen, [v.destino.lat, v.destino.lon], 32, curva.get(v.id))
    lineas.push({
      type: 'Feature',
      properties: {
        id: v.id,
        color: NIVELES[v.nivel].color, // el mismo color de su insignia en el listado
        ancho: sel ? 5 : 2,
        opacidad: elegido && !sel ? 0.2 : sel ? 1 : 0.75,
        orden: sel ? 1 : 0,
      },
      geometry: { type: 'LineString', coordinates: coords },
    })
    destinos.set(v.destino.iata, { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: coords.at(-1) } })
  }
  return {
    lineas: { type: 'FeatureCollection', features: lineas },
    puntos: { type: 'FeatureCollection', features: [...destinos.values()] },
  }
}

function dibujar() {
  if (!listo) return
  const { lineas, puntos } = geojson()
  mapa.getSource('rutas').setData(lineas)
  mapa.getSource('destinos').setData(puntos)
}

function limitesDe(coords) {
  return coords.reduce((b, c) => b.extend(c), new mapboxgl.LngLatBounds(coords[0], coords[0]))
}

const vueloPorId = (id) => dibujables().find((v) => v.id === id)

const VACIO = { type: 'FeatureCollection', features: [] }

function agregarCapas() {
  const { lineas, puntos } = geojson()
  mapa.addSource('rutas', { type: 'geojson', data: lineas })
  mapa.addSource('destinos', { type: 'geojson', data: puntos })
  mapa.addSource('estela', { type: 'geojson', data: VACIO })
  // Fuente de modelo: posición y orientación se actualizan en el mismo cuadro (sin pasar por el worker).
  mapa.addSource('avion', { type: 'model', models: {} })
  const linea = {
    'line-color': ['get', 'color'],
    'line-width': ['get', 'ancho'],
    'line-opacity': ['get', 'opacidad'],
    'line-emissive-strength': 1, // que no se oscurezcan con la iluminación del estilo Standard
  }
  const diseno = { 'line-cap': 'round', 'line-sort-key': ['get', 'orden'] }
  mapa.addLayer({ id: 'rutas', type: 'line', source: 'rutas', layout: diseno, paint: linea })
  // Destinos en un color neutro: el riesgo lo dice cada raya, no el aeropuerto.
  mapa.addLayer({
    id: 'destinos', type: 'circle', source: 'destinos',
    paint: {
      'circle-radius': 3.5, 'circle-color': '#ffffff', 'circle-stroke-color': '#222', 'circle-stroke-width': 1,
      'circle-emissive-strength': 1,
    },
  })
  mapa.addLayer({
    id: 'estela', type: 'line', source: 'estela', layout: { 'line-cap': 'round' },
    paint: { 'line-color': '#ffffff', 'line-width': 3, 'line-opacity': 0.9, 'line-emissive-strength': 1 },
  })
  prepararAvion()

  mapa.on('click', 'rutas', (e) => emit('seleccionar-vuelo', e.features[0].properties.id))
  mapa.on('mousemove', 'rutas', (e) => {
    mapa.getCanvas().style.cursor = 'pointer'
    const v = vueloPorId(e.features[0].properties.id)
    if (v) {
      etiqueta.setLngLat(e.lngLat)
        .setText(`${v.numero} → ${v.destino.iata} · ${hora(v.salida_local)} · ${porcentaje(v.probabilidad)}`).addTo(mapa)
    }
  })
  mapa.on('mouseleave', 'rutas', () => {
    mapa.getCanvas().style.cursor = ''
    etiqueta.remove()
  })
}

// ---------- Animación del vuelo ----------
let avionListo = false

/**
 * Mapbox carga la clase de la fuente de modelos de forma asíncrona: justo después de addSource solo hay un objeto
 * provisional sin setModels. Se espera a que exista para agregar la capa del avión (antes, el renderizador falla).
 */
function prepararAvion() {
  if (!mapa) return
  const fuente = mapa.getSource('avion')
  if (!fuente?.setModels) {
    fuente?.startLoad?.() // el provisional solo carga el módulo real cuando se le pide
    requestAnimationFrame(prepararAvion)
    return
  }
  mapa.addLayer({
    id: 'avion', type: 'model', source: 'avion', slot: 'top',
    paint: {
      'model-type': 'common-3d',
      'model-scale': ['interpolate', ['exponential', 0.5], ['zoom'],
        2, ['literal', [ESCALA_Z14 * 4096, ESCALA_Z14 * 4096, ESCALA_Z14 * 4096]],
        14, ['literal', [ESCALA_Z14, ESCALA_Z14, ESCALA_Z14]],
        20, ['literal', [ESCALA_Z14 / 64, ESCALA_Z14 / 64, ESCALA_Z14 / 64]]],
      'model-translation': [0, 0, ['feature-state', 'altura']],
      'model-color': '#f4f6fa', 'model-color-mix-intensity': 0.2, 'model-emissive-strength': 0.6,
    },
  })
  avionListo = true
  if (props.vuelo) animarVuelo(props.vuelo)
}

const rad = (g) => (g * Math.PI) / 180
const suave = (t) => t * t * (3 - 2 * t) // smoothstep 0→1
const acotar = (v, a, b) => Math.min(b, Math.max(a, v))
const metrosPorPx = (lat, zoom) => (78271.517 * Math.cos(rad(lat))) / 2 ** zoom
const difAngulo = (a, b) => ((b - a + 540) % 360) - 180 // giro más corto de a a b, en grados
// Mezcla exponencial independiente de los fps: la misma suavidad a 30 o a 144 cuadros por segundo.
const hacia = (actual, objetivo, dt, rapidez) => actual + (objetivo - actual) * (1 - Math.exp(-dt * rapidez))

function rumbo([lo1, la1], [lo2, la2]) {
  const y = Math.sin(rad(lo2 - lo1)) * Math.cos(rad(la2))
  const x = Math.cos(rad(la1)) * Math.sin(rad(la2)) - Math.sin(rad(la1)) * Math.cos(rad(la2)) * Math.cos(rad(lo2 - lo1))
  return (Math.atan2(y, x) * 180) / Math.PI
}

function distKm([lo1, la1], [lo2, la2]) {
  const a = Math.sin(rad(la2 - la1) / 2) ** 2 + Math.cos(rad(la1)) * Math.cos(rad(la2)) * Math.sin(rad(lo2 - lo1) / 2) ** 2
  return 12742 * Math.asin(Math.sqrt(a))
}

/** Punto y rumbo a una fracción f (0–1) del recorrido, medido por distancia. */
function puntoEn(coords, acum, f) {
  const objetivo = f * acum.at(-1)
  let i = acum.findIndex((d) => d >= objetivo)
  if (i <= 0) i = 1
  const a = coords[i - 1]
  const b = coords[i]
  const tramo = acum[i] - acum[i - 1] || 1
  const u = acotar((objetivo - acum[i - 1]) / tramo, 0, 1)
  return { pos: [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u], rumbo: rumbo(a, b), i }
}

/** Altura relativa (0–1): despega en el primer 25 %, crucero, aterriza en el último 25 %. */
const perfil = (f) => suave(acotar(f / 0.25, 0, 1)) * suave(acotar((1 - f) / 0.25, 0, 1))

/** Coloca el avión: posición, orientación [alabeo, cabeceo, rumbo] y altura sobre el suelo. */
function colocarAvion(pos, rumboGrados, cabeceo = 0, alabeo = 0, alturaRel = 0) {
  mapa.getSource('avion').setModels({
    avion: { uri: MODELO, position: pos, orientation: [-cabeceo, -alabeo, rumboGrados + GIRO_MODELO] },
  })
  // La altura va en metros; se escala con el zoom para que se vea igual de alta a cualquier acercamiento.
  mapa.setFeatureState(ID_AVION, { altura: (8 + ALTURA_PX * alturaRel) * metrosPorPx(pos[1], mapa.getZoom()) })
}

function detenerAnimacion() {
  if (animacion) cancelAnimationFrame(animacion)
  animacion = null
}

// ---------- Efectos de clima sobre origen y destino ----------
let efectos = [] // marcadores con la escena animada de cada aeropuerto
const SPC_TORMENTA = new Set(['SLGT', 'ENH', 'MDT', 'HIGH'])

/** Qué mostrar según el pronóstico: tipos de efecto y un resumen en texto. */
function condiciones(c) {
  if (!c) return { tipos: [], texto: '' }
  const cod = c.codigo ?? 0
  const tormenta = cod >= 95 || SPC_TORMENTA.has(c.spc)
  const nieve = (c.nieve ?? 0) > 0 || (cod >= 71 && cod <= 77) || cod === 85 || cod === 86
  const lluvia = !nieve && (tormenta || (c.precipitacion ?? 0) >= 0.3 || (cod >= 51 && cod <= 67) || (cod >= 80 && cod <= 82))
  const niebla = cod === 45 || cod === 48
  const viento = (c.rafagas ?? 0) >= 50
  const tipos = []
  const texto = []
  if (tormenta) { tipos.push('tormenta'); texto.push(cod >= 95 ? 'Tormenta eléctrica' : 'Riesgo de tormentas') }
  if (lluvia) { tipos.push('lluvia'); texto.push(`Lluvia ${c.precipitacion ?? 0} mm/h`) }
  if (nieve) { tipos.push('nieve'); texto.push(`Nieve ${c.nieve ?? 0} cm/h`) }
  if (niebla) { tipos.push('niebla'); texto.push('Niebla') }
  if (viento) { tipos.push('viento'); texto.push(`Ráfagas ${Math.round(c.rafagas)} km/h`) }
  return { tipos, texto: texto.join(' · ') }
}

function escena(iata, c) {
  const { tipos, texto } = condiciones(c)
  if (!tipos.length) return null
  const el = document.createElement('div')
  el.className = `clima-efecto ${tipos.join(' ')}`
  // Posiciones fijas pseudoaleatorias: la escena se ve igual cada vez.
  const particulas = (clase, n, signo = '') => Array.from({ length: n }, (_, i) =>
    `<span class="${clase}" style="left:${(i * 37) % 92 + 4}%;animation-delay:${((i * 0.29) % 1.3).toFixed(2)}s">${signo}</span>`).join('')
  // Tres franjas horizontales escalonadas (niebla o ráfagas de viento).
  const capas = (clase, retraso) => [32, 42, 52].map((y, i) =>
    `<span class="${clase}" style="top:${y}px;animation-delay:${i * retraso}s"></span>`).join('')
  el.innerHTML = `<div class="escena" aria-hidden="true">
      <span class="nube">☁</span>
      ${tipos.includes('lluvia') ? particulas('gota', 12) : ''}
      ${tipos.includes('nieve') ? particulas('copo', 10, '❄') : ''}
      ${tipos.includes('tormenta') ? '<span class="rayo">⚡</span>' : ''}
      ${tipos.includes('niebla') ? capas('banda', 1) : ''}
      ${tipos.includes('viento') ? capas('rafaga', 0.45) : ''}
    </div><div class="etiqueta-clima"></div>`
  el.querySelector('.etiqueta-clima').textContent = `${iata}: ${texto}`
  return el
}

function mostrarClima(v) {
  quitarClima()
  const puntos = [[props.aeropuerto.iata, [props.aeropuerto.lon, props.aeropuerto.lat], v.contexto?.origen],
    [v.destino.iata, [v.destino.lon, v.destino.lat], v.contexto?.destino]]
  for (const [iata, pos, c] of puntos) {
    const el = escena(iata, c)
    if (el) efectos.push(new mapboxgl.Marker({ element: el, anchor: 'bottom', offset: [0, -14] }).setLngLat(pos).addTo(mapa))
  }
}

function quitarClima() {
  for (const m of efectos) m.remove()
  efectos = []
}

function limpiarAvion() {
  detenerAnimacion()
  quitarClima()
  if (!listo) return
  if (avionListo) mapa.getSource('avion').setModels({})
  mapa.getSource('estela').setData(VACIO)
}

/** Espera a que el mapa termine de cargar teselas (o 4 s como máximo) para no arrancar con tirones. */
function cuandoQuieto(fn) {
  let hecho = false
  const correr = () => {
    if (hecho) return
    hecho = true
    fn()
  }
  mapa.once('idle', correr)
  setTimeout(correr, 4000)
}

function animarVuelo(v) {
  limpiarAvion()
  if (!listo || !avionListo || !v?.destino || v.destino.lat == null) return
  const origen = [props.aeropuerto.lat, props.aeropuerto.lon]
  const coords = arco(origen, [v.destino.lat, v.destino.lon], 128, curvaturas().get(v.id) ?? 0.2)
  const acum = [0]
  for (let i = 1; i < coords.length; i++) acum.push(acum[i - 1] + distKm(coords[i - 1], coords[i]))
  const rumbo0 = rumbo(coords[0], coords[4])
  // Vista lejana fija con toda la ruta, orientada para que el avión avance hacia arriba de la pantalla.
  // Deja libre el espacio del listado (izquierda) y de la tarjeta de factores (derecha).
  const amplio = mapa.getContainer().clientWidth > 900
  const vista = {
    padding: amplio ? { top: 100, bottom: 100, left: 380, right: 450 } : 60,
    pitch: 45, bearing: rumbo(coords[0], coords.at(-1)), maxZoom: 7,
  }

  if (reducirMovimiento) {
    colocarAvion(coords.at(-1), rumbo(coords.at(-2), coords.at(-1)))
    mapa.fitBounds(limitesDe(coords), { ...vista, animate: false })
    mostrarClima(v)
    return
  }

  colocarAvion(coords[0], rumbo0)
  mapa.fitBounds(limitesDe(coords), { ...vista, duration: 2500, essential: true })
  mostrarClima(v)
  mapa.once('moveend', () => cuandoQuieto(() => {
    if (props.vuelo?.id !== v.id) return
    // Más lento y suave: 16–30 s según la distancia.
    const duracion = acotar(14000 + acum.at(-1) * 5, 16000, 30000)
    let inicio = null
    let antes = null
    let ultimaEstela = 0
    // Estado suavizado del avión.
    let rumboAvion = rumbo0
    let cabeceo = 0
    let alabeo = 0
    const paso = (ahora) => {
      inicio ??= ahora
      const dt = Math.min(0.1, (ahora - (antes ?? ahora)) / 1000) // segundos; acotado si la pestaña se pausó
      antes = ahora
      const t = Math.min(1, (ahora - inicio) / duracion)
      const f = (1 - Math.cos(Math.PI * t)) / 2 // easeInOutSine: arranca y frena con suavidad
      const { pos, rumbo: r, i } = puntoEn(coords, acum, f)
      const h = perfil(f)

      // Rumbo suavizado; el alabeo (inclinación de alas) acompaña la velocidad del giro.
      const giro = difAngulo(rumboAvion, r)
      rumboAvion += giro * (1 - Math.exp(-dt * 3))
      alabeo = hacia(alabeo, acotar(giro * 4, -25, 25), dt, 2)
      // Cabeceo: nariz arriba al despegar y abajo al aterrizar.
      const pendiente = (perfil(Math.min(1, f + 0.01)) - perfil(Math.max(0, f - 0.01))) / 0.02
      cabeceo = hacia(cabeceo, acotar(pendiente * 4, -8, 10), dt, 2)
      colocarAvion(pos, rumboAvion, cabeceo, alabeo, h)

      if (ahora - ultimaEstela > 80) { // la estela no necesita 60 actualizaciones por segundo
        ultimaEstela = ahora
        mapa.getSource('estela').setData({
          type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: [...coords.slice(0, i), pos] },
        })
      }
      if (t < 1) {
        animacion = requestAnimationFrame(paso)
      } else {
        animacion = null
        colocarAvion(pos, rumboAvion) // aterrizado: alas niveladas
      }
    }
    animacion = requestAnimationFrame(paso)
  }))
}

function vistaGeneral() {
  limpiarAvion()
  if (!mapa) return
  mapa.easeTo({ center: [props.aeropuerto.lon, props.aeropuerto.lat], zoom: 3.2, pitch: 0, bearing: 0, duration: 1500 })
}

function crearMarcadorOrigen(a) {
  const el = document.createElement('div')
  el.className = 'marcador-origen'
  el.innerHTML = `<span class="punto"></span><span class="nombre"></span>`
  el.querySelector('.nombre').textContent = `${a.iata} · ${a.nombre} (origen)`
  return new mapboxgl.Marker({ element: el, anchor: 'left', offset: [-9, 0] }).setLngLat([a.lon, a.lat])
}

onMounted(() => {
  if (sinToken) return
  mapboxgl.accessToken = TOKEN
  mapa = new mapboxgl.Map({
    container: contenedor.value,
    style: ESTILO,
    center: [props.aeropuerto.lon, props.aeropuerto.lat],
    zoom: 3.2,
    customAttribution: [
      'Clima: <a href="https://open-meteo.com/" target="_blank" rel="noopener">API Open-Meteo</a> (CC BY 4.0)',
      'Datos históricos: <a href="https://www.transtats.bts.gov/" target="_blank" rel="noopener">BTS, U.S. DOT</a>',
      'Vuelos en vivo: <a href="https://aerodatabox.com/" target="_blank" rel="noopener">AeroDataBox</a>',
      '747: <a href="https://poly.pizza/m/49CLof4tP2V" target="_blank" rel="noopener">Miha Lunar</a> (CC-BY)',
    ],
  })
  marcadorOrigen = crearMarcadorOrigen(props.aeropuerto).addTo(mapa)
  mapa.on('load', () => {
    agregarCapas()
    listo = true
    if (props.vuelo) animarVuelo(props.vuelo)
  })
  // El tamaño del mapa cambia al plegar el panel.
  observador = new ResizeObserver(() => mapa.resize())
  observador.observe(contenedor.value)
})

onBeforeUnmount(() => {
  detenerAnimacion()
  observador?.disconnect()
  mapa?.remove()
})

watch(() => props.vuelos, dibujar)
watch(() => props.aeropuerto, (a) => {
  if (!mapa) return
  marcadorOrigen.remove()
  marcadorOrigen = crearMarcadorOrigen(a).addTo(mapa)
  limpiarAvion()
  mapa.easeTo({ center: [a.lon, a.lat], zoom: 3.2, pitch: 0, bearing: 0 })
})
watch(() => props.vuelo?.id, (id, antes) => {
  dibujar() // resalta la raya del vuelo elegido y atenúa las demás
  if (id) animarVuelo(props.vuelo)
  else if (antes) vistaGeneral()
})
</script>

<template>
  <div ref="contenedor" class="mapa" role="region" aria-label="Mapa de rutas desde el aeropuerto de origen">
    <p v-if="sinToken" class="mapa-aviso">
      Falta configurar Mapbox: agrega <code>VITE_MAPBOX_TOKEN</code> y <code>VITE_MAPBOX_STYLE</code> al
      <code>.env</code> de la raíz del proyecto y reinicia <code>npm run dev</code>.
    </p>
  </div>
</template>
