# Chatbot IFTS 29

## Descripción del proyecto

Este proyecto consiste en el desarrollo de un chatbot para el **IFTS 29**, cuyo objetivo es facilitar el acceso a información y documentación institucional mediante herramientas de desarrollo e integración con servicios externos.

La solución utiliza una arquitectura basada en Python y contempla la integración de las siguientes tecnologías:

- **Python:** lenguaje de programación principal.
- **FastAPI:** framework para el desarrollo de servicios y API.
- **Supabase:** plataforma para la gestión y almacenamiento de datos.
- **Google Drive API:** integración para acceder a archivos y documentación almacenados en Google Drive.

El proyecto se organiza de manera modular, buscando facilitar el mantenimiento, las pruebas y la incorporación de nuevas funcionalidades.

## Tecnologías utilizadas

| Tecnología | Propósito |
|---|---|
| Python | Desarrollo de la aplicación |
| FastAPI | Creación de servicios y endpoints |
| Supabase | Gestión de datos |
| Google Drive API | Acceso a documentación en Drive |
| Git y GitHub | Control de versiones y trabajo colaborativo |
| `venv` | Aislamiento de dependencias |
| `python-dotenv` | Lectura de variables de entorno, si está incluida en las dependencias |

## Requisitos previos

Antes de comenzar, es necesario contar con:

- Python 3 instalado. Se recomienda utilizar la versión definida para el proyecto.
- Git instalado.
- Acceso al repositorio del proyecto.
- Credenciales y permisos necesarios para Supabase y Google Cloud.
- Acceso a Google Drive y a los archivos que se utilizarán durante las pruebas, según los permisos configurados.

## Instalación y configuración

### 1. Clonar el repositorio

Desde una terminal, ejecutar:

```bash
git clone URL_DEL_REPOSITORIO
```

Ingresar al directorio del proyecto:

```bash
cd NOMBRE_DEL_PROYECTO
```

### 2. Cambiar a la rama de trabajo

Consultar las ramas disponibles:

```bash
git branch -a
```

Cambiar a la rama correspondiente al desarrollo:

```bash
git switch NOMBRE_DE_LA_RAMA
```

Reemplazar `NOMBRE_DE_LA_RAMA` por el nombre real de la rama asignada.

### 3. Crear el entorno virtual

Crear un entorno virtual para mantener aisladas las dependencias del proyecto:

```bash
python -m venv venv
```

### 4. Activar el entorno virtual

En Windows, utilizando CMD:

```bat
venv\Scripts\activate.bat
```

En Windows, utilizando PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

En Linux o macOS:

```bash
source venv/bin/activate
```

Una vez activado, se recomienda actualizar `pip`:

```bash
python -m pip install --upgrade pip
```

### 5. Instalar las dependencias

Instalar los paquetes necesarios a partir del archivo de dependencias:

```bash
pip install -r requirements.txt
```

Si el archivo `requirements.txt` no se encuentra en el repositorio, verificar con el equipo cuál es el procedimiento establecido para instalar las dependencias.

### 6. Configurar las variables de entorno

Crear un archivo `.env` en el directorio raíz del proyecto, tomando como referencia `.env.example`, si está disponible.

Ejemplo:

```env
SUPABASE_URL=
SUPABASE_KEY=
GOOGLE_APPLICATION_CREDENTIALS=
```

Completar las variables con los valores correspondientes al entorno de desarrollo.

**Importante:** los nombres de las variables deben coincidir con los utilizados en el código. Este ejemplo es orientativo y debe ajustarse a la configuración real del proyecto.

Para Google Cloud, `GOOGLE_APPLICATION_CREDENTIALS` normalmente indica la ruta al archivo JSON de credenciales de una cuenta de servicio, si esa es la modalidad de autenticación utilizada.

No subir al repositorio el archivo `.env`, las claves privadas ni los archivos de credenciales. Se recomienda incluirlos en `.gitignore` y compartirlos únicamente mediante los mecanismos seguros definidos por el equipo.

## Ejecución de pruebas

El proyecto incluye scripts de prueba para verificar las integraciones y funcionalidades desarrolladas.

### Prueba de integración con Google Drive

Para ejecutar el script `probar_drive.py`, activar previamente el entorno virtual, configurar las credenciales y ejecutar:

```bash
python probar_drive.py
```

La prueba permite comprobar el funcionamiento de la integración con Google Drive, de acuerdo con las operaciones implementadas en el script.

Si se produce un error, verificar:

- Que las credenciales de Google Cloud sean válidas.
- Que la API de Google Drive esté habilitada en el proyecto de Google Cloud.
- Que la cuenta utilizada tenga permisos sobre los archivos o carpetas necesarios.
- Que las variables de entorno y las rutas estén correctamente configuradas.

### Pruebas adicionales

A medida que avance el desarrollo, se incorporarán o documentarán pruebas para verificar los endpoints de FastAPI, la conexión con Supabase y las funcionalidades principales del chatbot.

## Estructura del proyecto

La estructura definitiva debe reflejar los directorios y archivos existentes en el repositorio. Como referencia, podría organizarse de la siguiente manera:

```text
NOMBRE_DEL_PROYECTO/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── venv/
├── probar_drive.py
└── app/
    ├── main.py
    └── ...
```

Esta estructura es ilustrativa y debe actualizarse según la organización real del código. El entorno virtual `venv/` no debe subirse al repositorio.

## Buenas prácticas de desarrollo

Para mantener el proyecto organizado y facilitar el trabajo colaborativo, se recomienda:

- Trabajar sobre la rama asignada antes de realizar cambios.
- Utilizar ramas específicas para nuevas funcionalidades o correcciones.
- Mantener actualizados los archivos `README.md` y `requirements.txt`.
- No incluir credenciales, claves privadas ni datos sensibles en Git.
- Ejecutar las pruebas correspondientes antes de enviar cambios.
- Realizar commits con mensajes claros y descriptivos.
- Documentar las configuraciones necesarias para reproducir el entorno de desarrollo.

## Control de versiones

El proyecto utiliza Git para registrar los cambios y GitHub para alojar el repositorio y facilitar el trabajo colaborativo.

Flujo básico de trabajo:

```bash
git status
git add .
git commit -m "Descripción de los cambios"
git push
```

Antes de ejecutar `git add .`, verificar que no se incluyan archivos sensibles, entornos virtuales, archivos temporales ni otros elementos excluidos del repositorio.

## Estado del proyecto

**Etapa actual:** preparación del entorno de desarrollo e integración con servicios externos.

Las funcionalidades y el estado de avance se actualizarán a medida que se incorporen y validen nuevos componentes.

## Autores

Proyecto desarrollado en el marco de actividades académicas y de desarrollo vinculadas al **IFTS 29**.
