import type { RespuestaChat, SolicitudChat } from './tipos';

export interface OpcionesEnvio {
  signal?: AbortSignal;
  /**
   * Se llama una vez si la respuesta tarda más de lo normal. En el plan gratuito el backend
   * puede estar "dormido" (RNF04) y la primera consulta demora; la interfaz avisa que espere.
   */
  alDemorarse?: () => void;
}

/** Único punto de acceso a la API. Hay una implementación real y una simulada. */
export interface ClienteChat {
  enviar(solicitud: SolicitudChat, opciones?: OpcionesEnvio): Promise<RespuestaChat>;
}

/** Milisegundos a partir de los cuales se considera que el backend está arrancando en frío. */
export const DEMORA_ARRANQUE_MS = 4000;
