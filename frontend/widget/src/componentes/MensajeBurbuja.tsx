import type { ContextoChat } from '../api';
import type { EstadoEncuesta, Mensaje } from '../hooks/useConversacion';
import { clases } from '../util/clases';
import { BloqueDerivacion } from './BloqueDerivacion';
import { EncuestaSatisfaccion } from './EncuestaSatisfaccion';
import { Recordatorio } from './Recordatorio';
import { SelectorContexto } from './SelectorContexto';
import { TarjetaFuente } from './TarjetaFuente';
import estilos from './MensajeBurbuja.module.css';

// "N.°" + espacio no separable + "29": con el separador de palabras (U+2060) tampoco se corta
// entre "N." y "°", que es lo que pasa en la captura.
const BIENVENIDA =
  '¡Hola! Soy el asistente virtual del IFTS N.\u2060°\u00A029. Elegí sobre qué tema necesitás ayuda.';

export interface AccionesMensaje {
  reintentar: (id: string) => void;
  elegirContexto: (texto: string, cambios: Partial<ContextoChat>) => void;
  completarEncuesta: (id: string, estado: Exclude<EstadoEncuesta, 'pendiente'>) => void;
}

interface Props {
  mensaje: Mensaje;
  /** Solo el último mensaje ofrece reintentar o elegir materia y comisión. */
  esUltimo: boolean;
  enviando: boolean;
  acciones: AccionesMensaje;
}

type MensajeBot = Exclude<Mensaje, { autor: 'estudiante' }>;

export function MensajeBurbuja({ mensaje, esUltimo, enviando, acciones }: Props) {
  if (mensaje.autor === 'estudiante') {
    return (
      <li className={clases(estilos.fila, estilos.filaEstudiante)}>
        <div className={clases(estilos.burbuja, estilos.estudiante)}>
          <span className="solo-lectores">Vos: </span>
          {mensaje.texto}
        </div>
      </li>
    );
  }

  const bloqueada = mensaje.tipo === 'respuesta' && mensaje.respuesta.estado === 'bloqueada';
  return (
    <li className={estilos.fila}>
      <div
        className={clases(
          estilos.burbuja,
          estilos.bot,
          mensaje.tipo === 'error' && estilos.error,
          bloqueada && estilos.netiqueta,
        )}
        data-para-revision={bloqueada || undefined}
      >
        <p className={estilos.etiqueta}>{bloqueada ? 'Aviso de netiqueta' : 'Bot'}</p>
        <ContenidoBot
          mensaje={mensaje}
          esUltimo={esUltimo}
          enviando={enviando}
          acciones={acciones}
        />
      </div>
    </li>
  );
}

interface PropsContenido {
  mensaje: MensajeBot;
  esUltimo: boolean;
  enviando: boolean;
  acciones: AccionesMensaje;
}

function ContenidoBot({ mensaje, esUltimo, enviando, acciones }: PropsContenido) {
  switch (mensaje.tipo) {
    case 'bienvenida':
      return <p className={estilos.texto}>{BIENVENIDA}</p>;
    case 'cargando':
      return (
        <>
          <p className={estilos.texto}>Estoy buscando información…</p>
          {mensaje.demorado && (
            <p className={estilos.nota}>
              El asistente se está iniciando y la primera consulta puede tardar unos segundos.
              Esperá, por favor.
            </p>
          )}
        </>
      );
    case 'respuesta': {
      const { respuesta } = mensaje;
      if (respuesta.estado === 'bloqueada') {
        // PARA REVISIÓN: pantalla sin diseño (RF10).
        return (
          <>
            <p className={estilos.texto}>{respuesta.respuesta}</p>
            <p className={estilos.nota}>
              Para que pueda ayudarte, escribí tu consulta con respeto y sin expresiones ofensivas.
            </p>
          </>
        );
      }
      return (
        <>
          <p className={estilos.texto}>{respuesta.respuesta}</p>
          {respuesta.fuentes.map((fuente) => (
            <TarjetaFuente key={fuente.documento_id} fuente={fuente} />
          ))}
          {respuesta.estado === 'requiere_contexto' && respuesta.campo_requerido && esUltimo && (
            <SelectorContexto
              campo={respuesta.campo_requerido}
              deshabilitado={enviando}
              alElegir={acciones.elegirContexto}
            />
          )}
          {respuesta.estado === 'sin_evidencia' && <BloqueDerivacion consulta={mensaje.consulta} />}
          {respuesta.advertencia_temporal && (
            <Recordatorio texto={respuesta.advertencia_temporal} />
          )}
        </>
      );
    }
    case 'error': {
      const { error } = mensaje;
      return (
        <>
          <p className={estilos.texto}>{error.message}</p>
          {error.tipo === 'limite' && error.reintentarEn !== undefined && (
            <p className={estilos.nota}>
              Podés volver a intentar en {error.reintentarEn} segundos.
            </p>
          )}
          {esUltimo && (
            <button
              type="button"
              className={estilos.reintentar}
              onClick={() => acciones.reintentar(mensaje.id)}
              disabled={enviando}
            >
              Reintentar
            </button>
          )}
        </>
      );
    }
    case 'encuesta':
      return (
        <EncuestaSatisfaccion
          estado={mensaje.estado}
          alTerminar={(estado) => acciones.completarEncuesta(mensaje.id, estado)}
        />
      );
  }
}
