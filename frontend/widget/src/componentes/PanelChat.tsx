import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { LARGO_MAXIMO_MENSAJE } from '../api';
import type { Tema } from '../config/temas';
import type { useConversacion } from '../hooks/useConversacion';
import { guardar, leer } from '../sesion/almacen';
import { clases } from '../util/clases';
import { AvisoPrivacidad } from './AvisoPrivacidad';
import { BarraTemas } from './BarraTemas';
import { IconoCerrar, IconoRobot } from './iconos';
import { MensajeBurbuja, type AccionesMensaje } from './MensajeBurbuja';
import estilos from './PanelChat.module.css';

const CLAVE_PRIVACIDAD = 'asistente.privacidadAceptada';

interface Props {
  id: string;
  abierto: boolean;
  /** Sin esta función (modo incrustado) no se muestra el botón de cerrar. */
  alCerrar?: () => void;
  conversacion: ReturnType<typeof useConversacion>;
  temas: readonly Tema[];
  className?: string | undefined;
}

const ID_TITULO = 'asistente-titulo';

export function PanelChat({ id, abierto, alCerrar, conversacion, temas, className }: Props) {
  const { mensajes, enviando, enviar, reintentar, completarEncuesta, huboConsultas } = conversacion;
  const [borrador, setBorrador] = useState('');
  const [verPrivacidad, setVerPrivacidad] = useState(() => leer(CLAVE_PRIVACIDAD) !== 'si');
  const campo = useRef<HTMLInputElement>(null);
  const lista = useRef<HTMLOListElement>(null);
  const desplazable = useRef<HTMLDivElement>(null);

  const acciones = useMemo<AccionesMensaje>(
    () => ({ reintentar, elegirContexto: enviar, completarEncuesta }),
    [reintentar, enviar, completarEncuesta],
  );

  const aceptarPrivacidad = () => {
    setVerPrivacidad(false);
    guardar(CLAVE_PRIVACIDAD, 'si');
    campo.current?.focus();
  };

  // Foco al campo solo cuando el panel pasa de cerrado a abierto. Si arranca abierto (modo
  // incrustado) no se enfoca: le robaría el foco a la página de Moodle al cargar.
  const estabaAbierto = useRef(abierto);
  useEffect(() => {
    if (abierto && !estabaAbierto.current) campo.current?.focus();
    estabaAbierto.current = abierto;
  }, [abierto]);

  // Sigue al último mensaje. Antes de la primera consulta queda arriba, para que el aviso de
  // privacidad se vea aunque el panel sea bajo.
  useEffect(() => {
    const elemento = desplazable.current;
    if (elemento) elemento.scrollTop = huboConsultas ? elemento.scrollHeight : 0;
  }, [mensajes, abierto, huboConsultas]);

  const alEnviar = (evento: FormEvent) => {
    evento.preventDefault();
    if (enviar(borrador)) setBorrador('');
    campo.current?.focus();
  };

  return (
    <section
      id={id}
      role="dialog"
      aria-labelledby={ID_TITULO}
      className={clases(estilos.panel, className)}
      hidden={!abierto}
    >
      <header className={estilos.encabezado}>
        <IconoRobot tamano={26} />
        <h2 id={ID_TITULO} className={estilos.titulo}>
          Asistente Virtual AppMinds
        </h2>
        {alCerrar && (
          <button type="button" className={estilos.cerrar} onClick={alCerrar}>
            <IconoCerrar />
            <span className="solo-lectores">Cerrar el asistente</span>
          </button>
        )}
      </header>

      {/* El aviso se desplaza con los mensajes: en pantallas bajas no les quita lugar. */}
      <div ref={desplazable} className={estilos.desplazable}>
        {verPrivacidad && <AvisoPrivacidad alAceptar={aceptarPrivacidad} />}

        <ol ref={lista} className={estilos.mensajes} aria-live="polite" aria-label="Conversación">
          {mensajes.map((mensaje, indice) => (
            <MensajeBurbuja
              key={mensaje.id}
              mensaje={mensaje}
              esUltimo={indice === mensajes.length - 1}
              enviando={enviando}
              acciones={acciones}
            />
          ))}
        </ol>
      </div>

      <BarraTemas
        temas={temas}
        compacta={huboConsultas}
        deshabilitada={enviando}
        alElegir={(tema) => enviar(tema.texto)}
      />

      <form className={estilos.pie} onSubmit={alEnviar}>
        <label htmlFor="asistente-consulta" className="solo-lectores">
          Tu consulta
        </label>
        <input
          ref={campo}
          id="asistente-consulta"
          className={estilos.campo}
          type="text"
          autoComplete="off"
          placeholder="Escribí tu consulta…"
          maxLength={LARGO_MAXIMO_MENSAJE}
          value={borrador}
          onChange={(evento) => setBorrador(evento.target.value)}
        />
        <button type="submit" className={estilos.enviar} disabled={enviando}>
          Enviar
        </button>
      </form>
    </section>
  );
}
