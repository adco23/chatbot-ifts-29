from enum import Enum
from typing import Dict, Any, Optional

class EstadoSesion(Enum):
    MENU_PRINCIPAL = "menu_principal"
    SUBMENU_PRIMEROS_PASOS = "submenu_1"
    SUBMENU_MATERIA = "submenu_2_materia"
    SUBMENU_COMISION = "submenu_2_comision"
    SUBMENU_MATERIA_DETALLE = "submenu_2_detalle"
    SUBMENU_ASISTENCIA = "submenu_3"
    SUBMENU_PLAN = "submenu_4"
    SUBMENU_TRAMITES = "submenu_5"
    SUBMENU_AYUDA_TECNICA = "submenu_6"
    MODO_RAG_LIBRE = "modo_libre"

sesiones_usuarios: Dict[str, Dict[str, Any]] = {}

sesiones = {}

def obtener_o_crear_sesion(user_id: str) -> dict:
    if user_id not in sesiones:
        sesiones[user_id] = {
            "estado": "menu_principal",  
            "contexto": {}
        }
    return sesiones[user_id]

def procesar_mensaje_menu(user_id: str, mensaje: str) -> dict:
    sesion = obtener_o_crear_sesion(user_id)
    estado_actual = sesion["estado"]
    texto_limpio = mensaje.strip().lower()

    # Comando global para volver al menú principal en cualquier momento
    if texto_limpio in ["menu", "volver", "inicio"]:
        sesion["estado"] = EstadoSesion.MENU_PRINCIPAL.value
        sesion["materia"] = None
        sesion["comision"] = None
        return {
            "respuesta": "Menú Principal:\n\n1. 🚀 Primeros pasos y Moodle\n2. 📚 Mi cursada (Horarios y clases)\n3. 📝 Asistencia y entregas\n4. ⚙️ Plan de estudios y carrera\n5. 📋 Trámites y SIU Guaraní\n6. 💻 Ayuda técnica\n0. 🔍 Ninguna de las anteriores (Consulta libre)",
            "nuevo_estado": sesion["estado"]
        }

    # ----------------------------------------------------
    # ESTADO: MENÚ PRINCIPAL
    # ----------------------------------------------------
    if estado_actual == EstadoSesion.MENU_PRINCIPAL.value:
        if texto_limpio == "1":
            sesion["estado"] = EstadoSesion.SUBMENU_PRIMEROS_PASOS.value
            return {
                "respuesta": "1️⃣ Primeros pasos y Moodle - Elegí una opción:\n\n1. Curso de ingreso y familiarización\n2. Cómo usar el campus Moodle\n3. Materiales para arrancar\n0. ↩️ Volver al menú principal",
                "nuevo_estado": sesion["estado"]
            }
        elif texto_limpio == "2":
            sesion["estado"] = EstadoSesion.SUBMENU_MATERIA.value
            return {
                "respuesta": "📚 Mi cursada - Seleccioná la materia:\n\n1. Técnicas de Programación\n2. Administración de Base de Datos\n3. Elementos de Análisis Matemático\n4. Lógica Computacional\n0. ↩️ Volver al menú principal",
                "nuevo_estado": sesion["estado"]
            }
        elif texto_limpio == "3":
            sesion["estado"] = EstadoSesion.SUBMENU_ASISTENCIA.value
            return {
                "respuesta": "3️⃣ Asistencia y entregas - Elegí una opción:\n\n1. Requisitos de asistencia (mínimo requerido)\n2. Faltas y justificaciones\n3. Entrega de actividades en Moodle\n4. Condición de alumno regular\n0. ↩️ Volver al menú principal",
                "nuevo_estado": sesion["estado"]
            }
        elif texto_limpio == "4":
            sesion["estado"] = EstadoSesion.SUBMENU_PLAN.value
            return {
                "respuesta": "4️⃣ Plan de estudios y carrera - Elegí una opción:\n\n1. Duración (3 años) y validez del título\n2. Plan de estudios y diseño curricular\n3. Correlatividades y régimen de aprobación\n0. ↩️ Volver al menú principal",
                "nuevo_estado": sesion["estado"]
            }
        elif texto_limpio == "5":
            sesion["estado"] = EstadoSesion.SUBMENU_TRAMITES.value
            return {
                "respuesta": "5️⃣ Trámites y SIU Guaraní - Elegí una opción:\n\n1. Pedir certificado de alumno regular\n2. Inscripción a materias y exámenes\n3. Actualizar datos personales y mail\n4. Calendario académico\n0. ↩️ Volver al menú principal",
                "nuevo_estado": sesion["estado"]
            }
        elif texto_limpio == "6":
            sesion["estado"] = EstadoSesion.SUBMENU_AYUDA_TECNICA.value
            return {
                "respuesta": "6️⃣ Ayuda técnica - Elegí una opción:\n\n1. Requisitos de PC o celular\n2. Problemas de conexión en exámenes\n3. Diferencia entre consulta académica y administrativa\n0. ↩️ Volver al menú principal",
                "nuevo_estado": sesion["estado"]
            }
        elif texto_limpio in ["0", "ninguna", "consulta libre"]:
            sesion["estado"] = EstadoSesion.MODO_RAG_LIBRE.value
            return {
                "respuesta": "🔍 Modo de consulta libre activado. Escribí tu pregunta directamente y el sistema la buscará en los documentos oficiales de Drive.",
                "nuevo_estado": sesion["estado"]
            }
        else:
            return {
                "respuesta": "⚠️ Opción no válida. Por favor, elegí un número del 1 al 6 o 0 para consulta libre.",
                "nuevo_estado": estado_actual
            }

# ----------------------------------------------------
    # ESTADO: SUBMENÚ 1 (PRIMEROS PASOS Y MOODLE)
    # ----------------------------------------------------
    elif estado_actual == EstadoSesion.SUBMENU_PRIMEROS_PASOS.value:
        if texto_limpio == "1":
            # Cambiamos opcionalmente el estado o devolvemos la respuesta de la opción 1
            return {
                "respuesta": "📌 **Curso de ingreso y familiarización:**\nEl curso de familiarización es una actividad introductoria fundamental para conocer la dinámica del instituto y el manejo del campus virtual. Es obligatorio para los ingresantes y todas sus pautas están disponibles en la plataforma.\n\n(Escribí 'menu' para volver al inicio).",
                "nuevo_estado": estado_actual
            }
        elif texto_limpio == "2":
            return {
                "respuesta": "📌 **Cómo usar el campus Moodle:**\nMoodle es nuestra plataforma oficial de cursada. Para usarla, ingresá con tus datos institucionales, recorriendo las secciones de tus materias donde encontrarás los foros, actividades y materiales de estudio.\n\n(Escribí 'menu' para volver al inicio).",
                "nuevo_estado": estado_actual
            }
        elif texto_limpio == "3":
            return {
                "respuesta": "📌 **Materiales para arrancar:**\nPara comenzar, asegurate de tener acceso a tu usuario del campus, los programas de las materias y los dispositivos necesarios para seguir las clases.\n\n(Escribí 'menu' para volver al inicio).",
                "nuevo_estado": estado_actual
            }
        elif texto_limpio in ["0", "menu", "volver"]:
            sesion["estado"] = EstadoSesion.MENU_PRINCIPAL.value
            return {
                "respuesta": "Menú Principal:\n\n1. 🚀 Primeros pasos y Moodle\n2. 📚 Mi cursada (Horarios y clases)\n3. 📝 Asistencia y entregas\n4. ⚙️ Plan de estudios y carrera\n5. 📋 Trámites y SIU Guaraní\n6. 💻 Ayuda técnica\n0. 🔍 Ninguna de las anteriores",
                "nuevo_estado": sesion["estado"]
            }
        else:
            return {
                "respuesta": "⚠️ Opción no válida en Primeros pasos. Elegí 1, 2, 3 o escribí 'menu' para volver al inicio.",
                "nuevo_estado": estado_actual
            }

    # ----------------------------------------------------
    # ESTADO: SUBMENÚ CURSADA (PASO A: MATERIA) -> LAS 4 MATERIAS
    # ----------------------------------------------------
    elif estado_actual == EstadoSesion.SUBMENU_MATERIA.value:
        if texto_limpio == "0":
            sesion["estado"] = EstadoSesion.MENU_PRINCIPAL.value
            return {
                "respuesta": "Menú Principal:\n\n1. 🚀 Primeros pasos y Moodle\n2. 📚 Mi cursada (Horarios y clases)\n3. 📝 Asistencia y entregas\n4. ⚙️ Plan de estudios y carrera\n5. 📋 Trámites y SIU Guaraní\n6. 💻 Ayuda técnica\n0. 🔍 Ninguna de las anteriores",
                "nuevo_estado": sesion["estado"]
            }
        
        # Mapeo de las 4 materias institucionales
        materias_map = {
            "1": "Técnicas de Programación",
            "tecnicas": "Técnicas de Programación",
            "2": "Administración de Base de Datos",
            "administracion": "Administración de Base de Datos",
            "base de datos": "Administración de Base de Datos",
            "abd": "Administración de Base de Datos",
            "3": "Elementos de Análisis Matemático",
            "analisis": "Elementos de Análisis Matemático",
            "matematico": "Elementos de Análisis Matemático",
            "4": "Lógica Computacional",
            "logica": "Lógica Computacional"
        }

        if texto_limpio in materias_map:
            sesion["materia"] = materias_map[texto_limpio]
            sesion["estado"] = EstadoSesion.SUBMENU_COMISION.value
            return {
                "respuesta": f"Has seleccionado: {sesion['materia']}.\n\n¿En qué comisión estás?\n\nA | B | C | D\n\n(Ingresá tu letra de comisión, por ejemplo: A)",
                "nuevo_estado": sesion["estado"]
            }
        else:
            return {
                "respuesta": "⚠️ Opción no válida. Seleccioná un número del 1 al 4 correspondiente a la materia o '0' para volver.",
                "nuevo_estado": estado_actual
            }

    # ----------------------------------------------------
    # ESTADO: SUBMENÚ CURSADA (PASO B: COMISIÓN)
    # ----------------------------------------------------
    elif estado_actual == EstadoSesion.SUBMENU_COMISION.value:
        comision = mensaje.strip().upper()
        if comision in ["A", "B", "C", "D"]:
            sesion["comision"] = comision
            sesion["estado"] = EstadoSesion.SUBMENU_MATERIA_DETALLE.value
            return {
                "respuesta": f"Perfecto. Materia: {sesion['materia']} - Comisión {sesion['comision']}:\n\n• Días y horarios de cursada específicos\n• Enlaces de clases y grabaciones (Moodle)\n• Cronograma oficial (archivos generados)\n\n(Escribí 'menu' para volver al inicio).",
                "nuevo_estado": sesion["estado"]
            }
        else:
            return {
                "respuesta": "⚠️ Comisión no válida. Ingresá una letra válida entre A y D (ej: A).",
                "nuevo_estado": estado_actual
            }

    # ----------------------------------------------------
    # ESTADOS DE SUBMENÚS 3, 4, 5 y 6 (INFORMACIÓN / OPCIONES INTERNAS)
    # ----------------------------------------------------
    elif estado_actual in [EstadoSesion.SUBMENU_ASISTENCIA.value, EstadoSesion.SUBMENU_PLAN.value, EstadoSesion.SUBMENU_TRAMITES.value, EstadoSesion.SUBMENU_AYUDA_TECNICA.value]:
        if texto_limpio in ["0", "menu", "volver"]:
            sesion["estado"] = EstadoSesion.MENU_PRINCIPAL.value
            return {
                "respuesta": "Menú Principal:\n\n1. 🚀 Primeros pasos y Moodle\n2. 📚 Mi cursada (Horarios y clases)\n3. 📝 Asistencia y entregas\n4. ⚙️ Plan de estudios y carrera\n5. 📋 Trámites y SIU Guaraní\n6. 💻 Ayuda técnica\n0. 🔍 Ninguna de las anteriores",
                "nuevo_estado": sesion["estado"]
            }
        else:
            return {
                "respuesta": "ℹ️ Información detallada de la sección seleccionada.\n\n(Escribí 'menu' para volver al menú principal o '0').",
                "nuevo_estado": estado_actual
            }

    # ----------------------------------------------------
    # ESTADO: MODO RAG LIBRE
    # ----------------------------------------------------
    elif estado_actual == EstadoSesion.MODO_RAG_LIBRE.value:
        return {
            "respuesta": f"[Motor RAG / Drive] Procesando consulta institucional: '{mensaje}'...",
            "nuevo_estado": sesion["estado"]
        }

    sesion["estado"] = EstadoSesion.MENU_PRINCIPAL.value
    return {
        "respuesta": "Volviendo al menú principal...",
        "nuevo_estado": sesion["estado"]
    }