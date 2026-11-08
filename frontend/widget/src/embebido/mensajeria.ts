import { ORIGENES_PADRE } from '../config/entorno';

/**
 * Mensajes del widget a la página que lo contiene (embed.js). Solo llevan estado y medidas,
 * nunca contenido de la conversación.
 */
export const FUENTE_MENSAJES = 'asistente-appminds';

export interface MensajeTamano {
  fuente: typeof FUENTE_MENSAJES;
  tipo: 'tamano';
  estado: 'cerrado' | 'abierto';
  /** Medidas deseadas en px. El loader las limita al tamaño de la ventana. */
  ancho: number;
  alto: number;
}

export const enIframe = window.parent !== window;

/**
 * Origen de la página contenedora, solo si está en la lista permitida (VITE_ORIGENES_PADRE).
 * embed.js lo pasa en `?padre=`; si el navegador expone `ancestorOrigins`, se exige que
 * coincida, para que nadie lo falsee desde la URL.
 */
function resolverOrigenPadre(): string | null {
  if (!enIframe) return null;
  const pedido = new URLSearchParams(window.location.search).get('padre');
  if (!pedido || !ORIGENES_PADRE.includes(pedido)) return null;
  const real = window.location.ancestorOrigins?.[0];
  if (real !== undefined && real !== pedido) return null;
  return pedido;
}

const origenPadre = resolverOrigenPadre();

export function avisarTamano(estado: MensajeTamano['estado'], ancho: number, alto: number): void {
  if (!origenPadre) return;
  const mensaje: MensajeTamano = {
    fuente: FUENTE_MENSAJES,
    tipo: 'tamano',
    estado,
    ancho: Math.ceil(ancho),
    alto: Math.ceil(alto),
  };
  // targetOrigin explícito: si el padre no es ese origen, el navegador descarta el mensaje.
  window.parent.postMessage(mensaje, origenPadre);
}
