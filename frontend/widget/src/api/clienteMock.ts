import { MATERIAS } from '../config/materias';
import { DEMORA_ARRANQUE_MS, type ClienteChat, type OpcionesEnvio } from './cliente';
import {
  ErrorApi,
  type CampoRequerido,
  type ContextoChat,
  type CorreoPrearmado,
  type DerivacionChat,
  type EstadoRespuesta,
  type FuenteChat,
  type RespuestaChat,
  type SolicitudChat,
} from './tipos';

/*
 * Cliente simulado para desarrollar sin backend. Imita el flujo de
 * backend/app/services/conversacion.py: reenvío de `contexto`, `consulta_pendiente`,
 * pedido de materia y comisión, recordatorio temporal (RF07) y derivación a un sector (RF05).
 *
 * Frases para probar estados a mano (sin distinguir mayúsculas ni tildes):
 *   "simular error"     → 503
 *   "simular limite"    → 429 con Retry-After
 *   "simular arranque"  → demora larga (arranque en frío, RNF04)
 *   "tonto", "idiota"…  → bloqueada (RF10)
 *   "equivalencias", "correlativas", "inscripción"… → derivada (RF05)
 */

const RECORDATORIO =
  'Verificá la información vigente en el canal institucional oficial del IFTS 29.';
const SIN_EVIDENCIA =
  'No cuento con evidencia suficiente para responder esa consulta. ' +
  'Verificá la información en el canal institucional oficial del IFTS 29.';
const UMBRAL = 0.6;

interface SectorSimulado {
  derivacion: DerivacionChat;
  palabrasClave: readonly string[];
}

/** Copiado de backend/app/config/derivaciones.json. */
const SECTORES: readonly SectorSimulado[] = [
  {
    derivacion: {
      sector: 'Bedelía',
      correo: 'bedelia@ifts29.edu.ar',
      horario: 'Lunes a Viernes de 18:30 a 21:30',
      responsable: 'Equipo de Bedelía',
    },
    palabrasClave: ['inscripcion', 'materias', 'correlativas', 'certificado de alumno regular'],
  },
  {
    derivacion: {
      sector: 'Secretaría Académica',
      correo: 'secretaria@ifts29.edu.ar',
      horario: 'Martes y Jueves de 19:00 a 21:00',
      responsable: 'Secretaría',
    },
    palabrasClave: ['equivalencias', 'titulo', 'pase', 'analitico'],
  },
];

/** Igual que `generar_correo_prearmado` del backend: plantilla fija, sin la consulta. */
const correoPrearmado = ({ correo }: DerivacionChat): CorreoPrearmado => ({
  destinatario: correo,
  asunto: 'Consulta de estudiante - IFTS 29',
  cuerpo:
    'Hola,\n\n' +
    'Quisiera realizar una consulta relacionada con gestiones estudiantiles del IFTS 29.' +
    '\n\nMuchas gracias.',
});

const TEMPORAL =
  /\b(fecha\w*|inscrip\w*|examen\w*|cronograma\w*|horario\w*|final(es)?|parcial\w*)\b/;
const OFENSIVO = /\b(tonto|tonta|idiota|estupid[oa]|inutil)\b/;
const ACADEMICA = /\b(parcial\w*|cronograma\w*|programa|examen\w*|final(es)?|mi cursada)\b/;
const REQUIERE_COMISION = /\b(parcial\w*|cronograma\w*|examen\w*|final(es)?|mi cursada)\b/;

interface Guion {
  patron: RegExp;
  respuesta: string;
  fuentes?: FuenteChat[];
}

const fuente = (id: number, titulo: string): FuenteChat => ({
  documento_id: id,
  drive_id: `simulado-${id}`,
  titulo,
  enlace: `https://example.org/documentos-simulados/${id}.pdf`,
  fragmento_ids: [id * 10],
});

const GUIONES: readonly Guion[] = [
  {
    patron: /\bplan de estudio/,
    respuesta:
      'El plan de estudio de la carrera tiene tres años. En primer año cursás Técnicas de ' +
      'Programación, Administración de Base de Datos, Lógica Computacional y Elementos de ' +
      'Análisis Matemático. Podés descargar el documento completo acá abajo.',
    fuentes: [fuente(1, 'Plan de estudio')],
  },
  {
    patron: /\b(primeros pasos|moodle|campus)\b/,
    respuesta:
      'Para empezar, entrá al Campus Virtual con el usuario que te enviaron por correo y ' +
      'revisá la sección "Avisos" de cada aula. Ahí el equipo docente publica las novedades.',
    fuentes: [fuente(2, 'Guía de primeros pasos en el Campus')],
  },
  {
    patron: /\b(asistencia|entregas?)\b/,
    respuesta:
      'Para mantener la regularidad necesitás el 75 % de asistencia. Las entregas se suben en ' +
      'la actividad de cada aula antes de la fecha indicada por el equipo docente.',
    fuentes: [fuente(3, 'Régimen de asistencia y regularidad')],
  },
  {
    patron: /\b(tramites?|siu)\b/,
    respuesta:
      'Las inscripciones a materias y finales se hacen en SIU Guaraní, dentro de las fechas ' +
      'que publica Bedelía. Si no podés ingresar, pedí el blanqueo de clave en Bedelía.',
    fuentes: [fuente(4, 'Instructivo de SIU Guaraní')],
  },
];

export class ClienteMock implements ClienteChat {
  async enviar(solicitud: SolicitudChat, opciones: OpcionesEnvio = {}): Promise<RespuestaChat> {
    const contexto = { ...solicitud.contexto };
    const pendiente = contexto.consulta_pendiente ?? null;
    const texto = normalizar(
      pendiente ? `${pendiente}\nAclaración: ${solicitud.mensaje}` : solicitud.mensaje,
    );

    const arranqueEnFrio = texto.includes('simular arranque');
    await esperar(arranqueEnFrio ? 9000 : 600 + Math.random() * 800, opciones);

    if (texto.includes('simular error')) {
      throw new ErrorApi(
        'servicio_no_disponible',
        'No se pudo completar la consulta. Intentá nuevamente.',
      );
    }
    if (texto.includes('simular limite')) {
      throw new ErrorApi(
        'limite',
        'Hiciste demasiadas consultas en poco tiempo. Esperá 30 segundos y volvé a intentar.',
        30,
      );
    }

    const advertencia = TEMPORAL.test(texto) ? RECORDATORIO : null;
    const responder = (
      estado: EstadoRespuesta,
      respuesta: string,
      extras: {
        fuentes?: FuenteChat[];
        campo?: CampoRequerido;
        contexto?: ContextoChat;
        derivacion?: DerivacionChat;
      } = {},
    ): RespuestaChat => ({
      estado,
      respuesta,
      fuentes: extras.fuentes ?? [],
      confianza: estado === 'respondida' ? 0.82 : 0,
      umbral: UMBRAL,
      advertencia_temporal: advertencia,
      contexto: extras.contexto ?? { ...contexto, consulta_pendiente: null },
      campo_requerido: extras.campo ?? null,
      derivacion: extras.derivacion ?? null,
      correo_prearmado: extras.derivacion ? correoPrearmado(extras.derivacion) : null,
    });

    if (OFENSIVO.test(texto)) return responder('bloqueada', 'No puedo procesar esa solicitud.');

    resolverContexto(contexto, texto);
    if (arranqueEnFrio) {
      return responder('respondida', 'Listo, ya estoy disponible. ¿En qué te ayudo?');
    }

    if (ACADEMICA.test(texto)) {
      const pedir = (campo: CampoRequerido) =>
        responder(
          'requiere_contexto',
          campo === 'materia_id'
            ? '¿Sobre qué materia es tu consulta?'
            : '¿Sobre qué comisión es tu consulta?',
          { campo, contexto: { ...contexto, consulta_pendiente: pendiente ?? solicitud.mensaje } },
        );
      if (contexto.materia_id == null) return pedir('materia_id');
      if (contexto.comision == null && REQUIERE_COMISION.test(texto)) return pedir('comision');
      const materia = MATERIAS.find((m) => m.id === contexto.materia_id)?.nombre ?? 'la materia';
      const comision = contexto.comision ? `, comisión ${contexto.comision}` : '';
      return responder(
        'respondida',
        `Según el cronograma de ${materia}${comision}, el primer parcial es en la semana 8 ` +
          'de cursada y el recuperatorio, dos semanas después.',
        { fuentes: [fuente(10 + (contexto.materia_id ?? 0), `Cronograma de ${materia}`)] },
      );
    }

    const guion = GUIONES.find((g) => g.patron.test(texto));
    if (guion) return responder('respondida', guion.respuesta, { fuentes: guion.fuentes ?? [] });

    const derivacion = buscarSector(texto);
    if (derivacion) {
      return responder(
        'derivada',
        'No cuento con evidencia suficiente para responder esa consulta. ' +
          `Podés consultar a ${derivacion.sector}.`,
        { derivacion },
      );
    }

    return responder('sin_evidencia', SIN_EVIDENCIA);
  }
}

function normalizar(texto: string): string {
  return texto
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase();
}

/**
 * Como `find_referral_department`: el primer sector cuyo nombre o palabra clave aparece como
 * palabra completa. `texto` ya viene normalizado (sin tildes, en minúsculas).
 */
function buscarSector(texto: string): DerivacionChat | undefined {
  const palabras = ` ${texto.replace(/[^\p{L}\d]+/gu, ' ')} `;
  return SECTORES.find(({ derivacion, palabrasClave }) =>
    [derivacion.sector, ...palabrasClave].some((clave) =>
      palabras.includes(` ${normalizar(clave)} `),
    ),
  )?.derivacion;
}

function resolverContexto(contexto: ContextoChat, texto: string): void {
  if (contexto.materia_id == null) {
    const [unica, ...otras] = MATERIAS.filter((m) => texto.includes(normalizar(m.nombre)));
    if (unica && otras.length === 0) contexto.materia_id = unica.id;
  }
  if (contexto.comision == null) {
    const letra = /\bcomision\s+([a-z]|\d{1,4}[a-z]?)(?!\w)/.exec(texto)?.[1];
    if (letra) contexto.comision = letra.toUpperCase();
  }
}

function esperar(ms: number, { signal, alDemorarse }: OpcionesEnvio): Promise<void> {
  return new Promise((resolver, rechazar) => {
    const demora =
      ms > DEMORA_ARRANQUE_MS ? setTimeout(() => alDemorarse?.(), DEMORA_ARRANQUE_MS) : 0;
    const fin = setTimeout(() => {
      clearTimeout(demora);
      resolver();
    }, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(fin);
      clearTimeout(demora);
      rechazar(new DOMException('Cancelado', 'AbortError'));
    });
  });
}
