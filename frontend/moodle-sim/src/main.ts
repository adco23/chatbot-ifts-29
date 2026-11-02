import './estilos.css';
import { iniciarDepuracion } from './depuracion';

/*
 * Simulación de un aula de Moodle para probar el embebido del asistente.
 * El widget se carga siempre desde OTRO origen (VITE_WIDGET_URL), como en el campus real.
 *
 *   ?modo=flotante                         embed.js, como lo pegaría un docente (por defecto)
 *   ?modo=flotante-iframe                  iframe fijo en línea, sin script
 *   ?modo=incrustado&ubicacion=seccion     iframe dentro de una sección del curso
 *   ?modo=incrustado&ubicacion=cajon       iframe dentro del cajón lateral de bloques
 *   &offset=80 &ancho=360                  ajustes (también desde el panel de depuración)
 */

export type Modo = 'flotante' | 'flotante-iframe' | 'incrustado';
export type Ubicacion = 'seccion' | 'cajon';

export interface Configuracion {
  widget: string;
  modo: Modo;
  ubicacion: Ubicacion;
  offset: number;
  ancho: number;
}

const BLOQUES = [
  { titulo: 'Bloque 1', progreso: 0 },
  { titulo: 'Bloque 2', progreso: 0 },
  { titulo: 'Bloque 3-A', progreso: 0 },
  { titulo: 'Bloque 3-B', progreso: 13 },
  { titulo: 'Bloque 4', progreso: 0 },
  { titulo: 'Encuesta', progreso: 0 },
  { titulo: 'Grabaciones de sincrónicos', progreso: 0 },
];

const configuracion = leerConfiguracion();
dibujarBloques();
iniciarCajon();
montarAsistente(configuracion);
iniciarDepuracion(configuracion);

function leerConfiguracion(): Configuracion {
  const parametros = new URLSearchParams(window.location.search);
  const modo = parametros.get('modo');
  const modoValido: Modo = modo === 'flotante-iframe' || modo === 'incrustado' ? modo : 'flotante';
  const entero = (nombre: string, porDefecto: number) => {
    const valor = Number(parametros.get(nombre));
    return parametros.has(nombre) && Number.isFinite(valor) && valor >= 0 ? valor : porDefecto;
  };
  const widget = (
    import.meta.env.VITE_WIDGET_URL ?? (import.meta.env.DEV ? 'http://localhost:5173' : '')
  ).replace(/\/+$/, '');
  return {
    widget,
    modo: modoValido,
    ubicacion: parametros.get('ubicacion') === 'cajon' ? 'cajon' : 'seccion',
    offset: entero('offset', 80),
    ancho: entero('ancho', modoValido === 'incrustado' ? 360 : 450),
  };
}

function dibujarBloques(): void {
  const grilla = document.getElementById('grilla');
  if (!grilla) return;
  for (const bloque of BLOQUES) {
    const tarjeta = document.createElement('a');
    tarjeta.href = '#';
    tarjeta.className = 'tarjeta-bloque';
    tarjeta.innerHTML = `
      <span class="titulo-bloque"></span>
      <span class="ilustracion" aria-hidden="true"></span>
      <span class="progreso-bloque"></span>
      <span class="franja" aria-hidden="true"></span>`;
    tarjeta.querySelector('.titulo-bloque')?.append(bloque.titulo);
    tarjeta.querySelector('.progreso-bloque')?.append(`Progreso: ${bloque.progreso}%`);
    grilla.append(tarjeta);
  }
}

function iniciarCajon(): void {
  const cajon = document.getElementById('cajon');
  const abrir = document.getElementById('abrir-cajon');
  const cerrar = document.getElementById('cerrar-cajon');
  if (!cajon || !abrir || !cerrar) return;
  const fijar = (abierto: boolean) => {
    cajon.hidden = !abierto;
    abrir.setAttribute('aria-expanded', String(abierto));
    document.body.classList.toggle('con-cajon', abierto);
  };
  abrir.addEventListener('click', () => fijar(true));
  cerrar.addEventListener('click', () => fijar(false));
  if (configuracion.modo === 'incrustado' && configuracion.ubicacion === 'cajon') fijar(true);
}

function montarAsistente({ widget, modo, ubicacion, offset, ancho }: Configuracion): void {
  if (!widget) return;

  if (modo === 'flotante') {
    // Exactamente lo que pegaría un docente en un bloque HTML (docs/embed.md, variante A).
    const script = document.createElement('script');
    script.src = `${widget}/embed.js`;
    script.dataset.offset = String(offset);
    script.defer = true;
    document.body.append(script);
    return;
  }

  const iframe = document.createElement('iframe');
  iframe.title = 'Asistente virtual';
  iframe.className = 'iframe-asistente';

  if (modo === 'flotante-iframe') {
    // Variante B de docs/embed.md: iframe fijo, sin script.
    iframe.src = `${widget}/?modo=flotante`;
    iframe.setAttribute(
      'style',
      `position: fixed; right: 0; bottom: ${offset}px; width: ${ancho}px; height: 680px;` +
        ` max-width: 100vw; max-height: calc(100vh - ${offset}px); border: 0;` +
        ' background: transparent; color-scheme: light; z-index: 2147483000;',
    );
    document.body.append(iframe);
    return;
  }

  // Variante C: incrustado.
  iframe.src = `${widget}/?modo=incrustado`;
  iframe.loading = 'lazy';
  iframe.setAttribute(
    'style',
    `width: 100%; max-width: ${ancho}px; min-width: 300px; height: 600px; border: 0;`,
  );
  const destino = document.getElementById(
    ubicacion === 'cajon' ? 'bloque-asistente' : 'seccion-asistente',
  );
  if (!destino) return;
  destino.hidden = false;
  destino.append(iframe);
}
