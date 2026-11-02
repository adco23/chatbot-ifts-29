import { DERIVACION_POR_DEFECTO, type Derivacion } from '../config/derivaciones';
import estilos from './BloqueDerivacion.module.css';

/** El texto de la consulta solo viaja en el enlace mailto: lo envía el estudiante. */
const LARGO_MAXIMO_CONSULTA = 500;

function enlaceCorreo({ sector, correo }: Derivacion, consulta: string): string {
  const recortada =
    consulta.length > LARGO_MAXIMO_CONSULTA
      ? `${consulta.slice(0, LARGO_MAXIMO_CONSULTA)}…`
      : consulta;
  const asunto = 'Consulta desde el asistente virtual';
  const cuerpo =
    `Hola, equipo de ${sector}:\n\n` +
    `No encontré la respuesta en el asistente virtual a esta consulta:\n\n"${recortada}"\n\n` +
    'Gracias.';
  return `mailto:${correo}?subject=${encodeURIComponent(asunto)}&body=${encodeURIComponent(cuerpo)}`;
}

// PARA REVISIÓN: pantalla sin diseño (RF05). El sector es fijo (Bedelía) porque el backend
// todavía no devuelve a quién derivar.
export function BloqueDerivacion({ consulta }: { consulta: string }) {
  const derivacion = DERIVACION_POR_DEFECTO;
  return (
    <div className={estilos.bloque} data-para-revision>
      <p className={estilos.titulo}>Podés consultarlo con {derivacion.sector}</p>
      <dl className={estilos.datos}>
        <dt>Sector</dt>
        <dd>{derivacion.sector}</dd>
        <dt>Correo</dt>
        <dd>{derivacion.correo}</dd>
        <dt>Horario</dt>
        <dd>{derivacion.horario}</dd>
      </dl>
      <a className={estilos.boton} href={enlaceCorreo(derivacion, consulta)}>
        Escribir a {derivacion.sector}
        <span className="solo-lectores"> (abre tu programa de correo con el mensaje armado)</span>
      </a>
      <p className={estilos.nota}>
        Se abre tu correo con el mensaje listo: revisalo antes de enviarlo.
      </p>
    </div>
  );
}
