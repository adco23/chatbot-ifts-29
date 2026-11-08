import { DEMORA_ARRANQUE_MS, type ClienteChat, type OpcionesEnvio } from './cliente';
import { ErrorApi, type RespuestaChat, type SolicitudChat, type TipoErrorApi } from './tipos';

/** Tope total de espera: cubre un arranque en frío del plan gratuito. */
const TIEMPO_MAXIMO_MS = 45000;

const MENSAJES: Record<Exclude<TipoErrorApi, 'limite'>, string> = {
  sesion_invalida: 'No se pudo identificar la sesión. Recargá la página y probá de nuevo.',
  validacion: 'No se pudo enviar la consulta. Revisá que no esté vacía ni sea demasiado larga.',
  servicio_no_disponible: 'No se pudo completar la consulta. Intentá nuevamente.',
  red: 'No hay conexión con el asistente. Revisá tu conexión y probá de nuevo.',
  tiempo_agotado: 'El asistente está tardando demasiado. Probá de nuevo en unos segundos.',
  desconocido: 'Ocurrió un error inesperado. Probá de nuevo.',
};

/**
 * Cliente contra el backend real. Nunca registra el texto de la consulta (RNF01).
 * Requiere que el backend habilite CORS para el origen del widget y el encabezado X-Sesion-Id.
 */
export class ClienteReal implements ClienteChat {
  private readonly urlBase: string;
  private readonly obtenerIdSesion: () => string;

  constructor(urlBase: string, obtenerIdSesion: () => string) {
    this.urlBase = urlBase.replace(/\/+$/, '');
    this.obtenerIdSesion = obtenerIdSesion;
  }

  async enviar(solicitud: SolicitudChat, opciones: OpcionesEnvio = {}): Promise<RespuestaChat> {
    const controlador = new AbortController();
    const cancelarExterno = () => controlador.abort();
    opciones.signal?.addEventListener('abort', cancelarExterno);
    let agotado = false;
    const temporizadorTope = setTimeout(() => {
      agotado = true;
      controlador.abort();
    }, TIEMPO_MAXIMO_MS);
    const temporizadorDemora = setTimeout(() => opciones.alDemorarse?.(), DEMORA_ARRANQUE_MS);

    try {
      let respuesta: Response;
      try {
        respuesta = await fetch(`${this.urlBase}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Sesion-Id': this.obtenerIdSesion() },
          body: JSON.stringify(solicitud),
          signal: controlador.signal,
          credentials: 'omit',
          referrerPolicy: 'strict-origin-when-cross-origin',
        });
      } catch (error) {
        if (agotado) throw new ErrorApi('tiempo_agotado', MENSAJES.tiempo_agotado);
        if (error instanceof DOMException && error.name === 'AbortError') throw error;
        throw new ErrorApi('red', MENSAJES.red);
      }

      if (respuesta.ok) return (await respuesta.json()) as RespuestaChat;
      throw await errorDesdeRespuesta(respuesta);
    } finally {
      clearTimeout(temporizadorTope);
      clearTimeout(temporizadorDemora);
      opciones.signal?.removeEventListener('abort', cancelarExterno);
    }
  }
}

async function errorDesdeRespuesta(respuesta: Response): Promise<ErrorApi> {
  const detalle = await leerDetalle(respuesta);
  switch (respuesta.status) {
    case 400:
      return new ErrorApi('sesion_invalida', MENSAJES.sesion_invalida);
    case 422:
      return new ErrorApi('validacion', MENSAJES.validacion);
    case 429: {
      const segundos = Number(respuesta.headers.get('Retry-After'));
      return new ErrorApi(
        'limite',
        typeof detalle === 'string' ? detalle : 'Hiciste demasiadas consultas en poco tiempo.',
        Number.isFinite(segundos) && segundos > 0 ? segundos : undefined,
      );
    }
    case 503:
      return new ErrorApi(
        'servicio_no_disponible',
        esDetalleConMensaje(detalle) ? detalle.mensaje : MENSAJES.servicio_no_disponible,
      );
    default:
      return new ErrorApi('desconocido', MENSAJES.desconocido);
  }
}

async function leerDetalle(respuesta: Response): Promise<unknown> {
  try {
    const cuerpo: unknown = await respuesta.json();
    return typeof cuerpo === 'object' && cuerpo !== null && 'detail' in cuerpo
      ? cuerpo.detail
      : undefined;
  } catch {
    return undefined;
  }
}

function esDetalleConMensaje(detalle: unknown): detalle is { mensaje: string } {
  return (
    typeof detalle === 'object' &&
    detalle !== null &&
    'mensaje' in detalle &&
    typeof detalle.mensaje === 'string'
  );
}
