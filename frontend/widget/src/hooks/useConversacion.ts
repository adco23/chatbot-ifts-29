import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ErrorApi,
  LARGO_MAXIMO_MENSAJE,
  type ClienteChat,
  type ContextoChat,
  type RespuestaChat,
  type SolicitudChat,
} from '../api';

export type EstadoEncuesta = 'pendiente' | 'enviada' | 'omitida';

export type Mensaje =
  | { id: string; autor: 'estudiante'; texto: string }
  | { id: string; autor: 'bot'; tipo: 'bienvenida' }
  | { id: string; autor: 'bot'; tipo: 'cargando'; demorado: boolean }
  | {
      id: string;
      autor: 'bot';
      tipo: 'respuesta';
      respuesta: RespuestaChat;
      /** Consulta original del estudiante; arma el correo de derivación (RF05). */
      consulta: string;
    }
  | { id: string; autor: 'bot'; tipo: 'error'; error: ErrorApi; solicitud: SolicitudChat }
  | { id: string; autor: 'bot'; tipo: 'encuesta'; estado: EstadoEncuesta };

let ultimoId = 0;
const nuevoId = () => `m${++ultimoId}`;

/**
 * Estado de la conversación. Vive solo en memoria: al recargar la página se pierde (RNF01).
 * El `contexto` que devuelve el backend se reenvía tal cual en la consulta siguiente.
 */
export function useConversacion(cliente: ClienteChat) {
  const [mensajes, setMensajes] = useState<Mensaje[]>(() => [
    { id: nuevoId(), autor: 'bot', tipo: 'bienvenida' },
  ]);
  const [enviando, setEnviando] = useState(false);
  const enviandoRef = useRef(false);
  const contexto = useRef<ContextoChat>({});
  const controlador = useRef<AbortController | null>(null);

  useEffect(() => () => controlador.current?.abort(), []);

  const reemplazar = useCallback((id: string, mensaje: Mensaje) => {
    setMensajes((actuales) => actuales.map((m) => (m.id === id ? mensaje : m)));
  }, []);

  const consultar = useCallback(
    async (solicitud: SolicitudChat, id: string) => {
      enviandoRef.current = true;
      setEnviando(true);
      const actual = new AbortController();
      controlador.current = actual;
      try {
        const respuesta = await cliente.enviar(solicitud, {
          signal: actual.signal,
          alDemorarse: () => reemplazar(id, { id, autor: 'bot', tipo: 'cargando', demorado: true }),
        });
        contexto.current = respuesta.contexto;
        const consulta = solicitud.contexto.consulta_pendiente ?? solicitud.mensaje;
        reemplazar(id, { id, autor: 'bot', tipo: 'respuesta', respuesta, consulta });
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        const errorApi =
          error instanceof ErrorApi
            ? error
            : new ErrorApi('desconocido', 'Ocurrió un error inesperado. Probá de nuevo.');
        reemplazar(id, { id, autor: 'bot', tipo: 'error', error: errorApi, solicitud });
      } finally {
        if (controlador.current === actual) {
          controlador.current = null;
          enviandoRef.current = false;
          setEnviando(false);
        }
      }
    },
    [cliente, reemplazar],
  );

  /**
   * Envía una consulta. `cambios` completa el contexto, por ejemplo con la materia o la
   * comisión elegida en el selector (RF03). Devuelve false si no se envió.
   */
  const enviar = useCallback(
    (texto: string, cambios: Partial<ContextoChat> = {}): boolean => {
      const mensaje = texto.trim().slice(0, LARGO_MAXIMO_MENSAJE);
      if (!mensaje || enviandoRef.current) return false;
      const idCargando = nuevoId();
      setMensajes((actuales) => [
        ...actuales,
        { id: nuevoId(), autor: 'estudiante', texto: mensaje },
        { id: idCargando, autor: 'bot', tipo: 'cargando', demorado: false },
      ]);
      void consultar({ mensaje, contexto: { ...contexto.current, ...cambios } }, idCargando);
      return true;
    },
    [consultar],
  );

  const reintentar = useCallback(
    (id: string) => {
      if (enviandoRef.current) return;
      // Solo el último mensaje: reintentar uno viejo dejaría la respuesta fuera de orden.
      const fallido = mensajes.at(-1);
      if (fallido?.id !== id || fallido.autor !== 'bot' || fallido.tipo !== 'error') return;
      reemplazar(id, { id, autor: 'bot', tipo: 'cargando', demorado: false });
      void consultar(fallido.solicitud, id);
    },
    [mensajes, consultar, reemplazar],
  );

  /** RF11: agrega la encuesta al final de la conversación (una sola vez). */
  const pedirEncuesta = useCallback(() => {
    setMensajes((actuales) =>
      actuales.some((m) => m.autor === 'bot' && m.tipo === 'encuesta')
        ? actuales
        : [...actuales, { id: nuevoId(), autor: 'bot', tipo: 'encuesta', estado: 'pendiente' }],
    );
  }, []);

  /**
   * El puntaje y el comentario no se envían: no hay endpoint todavía (ver
   * docs/api-contract.md). Solo se registra que la encuesta terminó.
   */
  const completarEncuesta = useCallback(
    (id: string, estado: Exclude<EstadoEncuesta, 'pendiente'>) => {
      reemplazar(id, { id, autor: 'bot', tipo: 'encuesta', estado });
    },
    [reemplazar],
  );

  const huboConsultas = mensajes.some((m) => m.autor === 'estudiante');
  const huboRespuestas = mensajes.some((m) => m.autor === 'bot' && m.tipo === 'respuesta');
  const encuestaPedida = mensajes.some((m) => m.autor === 'bot' && m.tipo === 'encuesta');

  return {
    mensajes,
    enviando,
    enviar,
    reintentar,
    pedirEncuesta,
    completarEncuesta,
    huboConsultas,
    huboRespuestas,
    encuestaPedida,
  };
}
