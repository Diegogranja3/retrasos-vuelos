<script setup>
// Panel de explicación: texto en lenguaje natural del vuelo elegido, redactado por IA a partir de los
// factores del modelo. El texto aparece letra por letra, con el borde de avance difuminado, y el panel crece
// de alto con suavidad (salvo con "reducir movimiento").
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { explicarVuelo } from '../api.js'

const props = defineProps({
  vuelo: { type: Object, required: true },
  origen: { type: String, required: true },
})

const LETRAS_POR_SEGUNDO = 55
const COLA = 14 // últimas letras que aparecen con un desvanecido

const completo = ref('') // texto entero (lectores de pantalla)
const visibles = ref(0) // cuántas letras se muestran
const modelo = ref(null)
const error = ref(null)
const tardando = ref(false) // el modelo gratuito a veces tarda; se avisa para que no parezca colgado
let aviso = null
const escribiendo = computed(() => visibles.value < completo.value.length)
const firme = computed(() => completo.value.slice(0, Math.max(0, visibles.value - COLA)))
const cola = computed(() => [...completo.value.slice(Math.max(0, visibles.value - COLA), visibles.value)])

// Alto animado: CSS no puede animar `height: auto`, así que se mide el contenido y se transiciona a ese valor.
const cuerpo = ref(null)
const contenido = ref(null)
let observador = null
let cuadro = null
const reducirMovimiento = window.matchMedia('(prefers-reduced-motion: reduce)').matches

function escribir() {
  if (reducirMovimiento) {
    visibles.value = completo.value.length
    return
  }
  const inicio = performance.now()
  const paso = (ahora) => {
    // Por tiempo, no por cuadro: la misma velocidad a 30 o a 144 fps.
    visibles.value = Math.min(completo.value.length, Math.floor(((ahora - inicio) / 1000) * LETRAS_POR_SEGUNDO))
    if (escribiendo.value) cuadro = requestAnimationFrame(paso)
  }
  cuadro = requestAnimationFrame(paso)
}

onMounted(async () => {
  observador = new ResizeObserver(() => {
    if (cuerpo.value && contenido.value) cuerpo.value.style.height = `${contenido.value.offsetHeight}px`
  })
  observador.observe(contenido.value)
  aviso = setTimeout(() => (tardando.value = true), 8000)
  try {
    const r = await explicarVuelo(props.origen, props.vuelo)
    modelo.value = r.modelo
    completo.value = r.texto
    escribir()
  } catch (e) {
    error.value = e.message
  } finally {
    clearTimeout(aviso)
  }
})
onBeforeUnmount(() => {
  cancelAnimationFrame(cuadro)
  clearTimeout(aviso)
  observador?.disconnect()
})
</script>

<template>
  <section class="asistente-ia vidrio" aria-labelledby="titulo-asistente">
    <header>
      <h2 id="titulo-asistente">Explicación</h2>
      <p class="sub">{{ vuelo.numero }} · {{ origen }} → {{ vuelo.destino?.iata }}</p>
    </header>
    <div ref="cuerpo" class="cuerpo-explicacion">
      <div ref="contenido">
        <p v-if="error" class="nota">No se pudo generar la explicación: {{ error }}</p>
        <p v-else-if="!completo" class="pensando" role="status"><span></span><span></span><span></span>
          {{ tardando ? 'El modelo gratuito está tardando más de lo normal…' : 'Analizando el vuelo…' }}
        </p>
        <template v-else>
          <!-- El texto completo para lectores de pantalla; el animado es solo visual. -->
          <p class="solo-lector">{{ completo }}</p>
          <p class="mensaje" aria-hidden="true">
            {{ firme }}<span
              v-for="(letra, i) in cola"
              :key="visibles - cola.length + i"
              class="letra-nueva"
              :style="{ opacity: escribiendo ? (i + 1) / cola.length : 1 }"
            >{{ letra }}</span>
          </p>
          <p class="nota" :class="{ oculta: escribiendo }">
            Redactado por IA ({{ modelo }}) a partir de los factores del modelo, no es la causa confirmada; puede contener errores.
          </p>
        </template>
      </div>
    </div>
  </section>
</template>
