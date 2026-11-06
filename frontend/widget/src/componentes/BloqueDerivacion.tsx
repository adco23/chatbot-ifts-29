import type { CorreoPrearmado, DerivacionChat } from '../api';
import { DERIVACION_POR_DEFECTO } from '../config/derivaciones';
import estilos from './BloqueDerivacion.module.css';

/** El texto de la consulta solo viaja en el enlace mailto: lo envía el estudiante. */
const LARGO_MAXIMO_CONSULTA = 500;

function enlaceMailto({ destinatario, asunto, cuerpo }: CorreoPrearmado): string {
  return `mailto:${destinatario}?subject=${encodeURIComponent(asunto)}&body=${encodeURIComponent(cuerpo)}`;
}

/** Respaldo para `sin_evidencia`: el backend no eligió sector, el correo se arma acá. */
function correoDeRespaldo({ sector, correo }: DerivacionChat, consulta: string): CorreoPrearmado {
  const recortada =
    consulta.length > LARGO_MAXIMO_CONSULTA
      ? `${consulta.slice(0, LARGO_MAXIMO_CONSULTA)}…`
      : consulta;
  return {
    destinatario: correo,
    asunto: 'Consulta desde el asistente virtual',
    cuerpo:
      `Hola, equipo de ${sector}:\n\n` +
      `No encontré la respuesta en el asistente virtual a esta consulta:\n\n"${recortada}"\n\n` +
      'Gracias.',
  };
}

interface Props {
  /** Consulta original del estudiante; solo se usa en el correo de respaldo. */
  consulta: string;
  /** Sector que eligió el backend (`estado = "derivada"`). Sin él se muestra el de respaldo. */
  derivacion?: DerivacionChat | null;
  /** Correo prearmado por el backend. Se usa solo si también viene `derivacion`. */
  correo?: CorreoPrearmado | null;
}

// PARA REVISIÓN: pantalla sin diseño (RF05).
export function BloqueDerivacion({ consulta, derivacion: delBackend, correo }: Props) {
  const derivacion = delBackend ?? DERIVACION_POR_DEFECTO;
  const mensaje = delBackend && correo ? correo : correoDeRespaldo(derivacion, consulta);
  return (
    <div className={estilos.bloque} data-para-revision>
      <p className={estilos.titulo}>Podés consultarlo con {derivacion.sector}</p>
      <dl className={estilos.datos}>
        <dt>Sector</dt>
        <dd>{derivacion.sector}</dd>
        {derivacion.responsable && (
          <>
            <dt>Contacto</dt>
            <dd>{derivacion.responsable}</dd>
          </>
        )}
        <dt>Correo</dt>
        <dd>{derivacion.correo}</dd>
        <dt>Horario</dt>
        <dd>{derivacion.horario}</dd>
      </dl>
      <a className={estilos.boton} href={enlaceMailto(mensaje)}>
        Escribir a {derivacion.sector}
        <span className="solo-lectores"> (abre tu programa de correo con el mensaje armado)</span>
      </a>
      <p className={estilos.nota}>
        Se abre tu correo con el mensaje listo: revisalo antes de enviarlo.
      </p>
    </div>
  );
}
