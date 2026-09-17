# MiPar Backend

API REST de MiPar construida con Python, FastAPI y SQLAlchemy. Incluye catálogo, inventario, cotización y registro de pedidos, solicitudes de calzado agotado, zonas y horarios de entrega, login del backoffice, dashboard, estados de venta y carga de imágenes a Vercel Blob.

## Ejecutar localmente

Requiere Python 3.12. En PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --port 8000
```

Abre `http://localhost:8000/docs`. En el frontend copia `.env.example` a `.env`, establece `NUXT_BACKEND_BASE_URL=http://localhost:8000` y reinicia `npm run dev`.

Si `NUXT_BACKEND_BASE_URL` queda vacío, el frontend conserva sus servicios simulados.

## Variables de producción

- `DATABASE_URL`: PostgreSQL administrado, por ejemplo Neon o Supabase Database.
- `FRONTEND_URL`: URL pública exacta del frontend.
- `ENVIRONMENT=production`.
- `JWT_SECRET`: valor largo y aleatorio.
- `ADMIN_USER` y `ADMIN_PASSWORD` (o `ADMIN_PASSWORD_HASH`).
- `BLOB_READ_WRITE_TOKEN`: Vercel la genera al conectar un Blob Store.

En el proyecto del frontend configura `NUXT_BACKEND_BASE_URL=https://tu-backend.vercel.app`.

## API

### Supabase desde Vercel

El backend usa `DATABASE_URL` si tiene valor; en su ausencia usa `POSTGRES_URL`
de la integración de Supabase. En Vercel configura `ENVIRONMENT=production` y
elimina cualquier `DATABASE_URL` de desarrollo que apunte a SQLite. Usa la URL
del Transaction Pooler (puerto 6543) en `POSTGRES_URL`, con `sslmode=require`.
El backend adapta esa URL a pg8000, verifica TLS y usa `NullPool` para que
Supabase gestione las conexiones. Vuelve a desplegar tras cambiar las variables.

No hacen falta las claves `SUPABASE_*` para esta conexión SQL. `JWT_SECRET`
sigue siendo una clave propia del backend, distinta de `SUPABASE_JWT_SECRET`.
El arranque crea las tablas y los datos iniciales; no migra los datos del SQLite
local. No guardes contraseñas ni URLs reales de conexión en el repositorio.

Las rutas públicas cubren productos, zonas, geocodificación inversa, cotizaciones, pedidos y solicitudes. Las rutas `/api/admin/*` exigen una sesión iniciada e incluyen productos, inventario, dashboard, pedidos, solicitudes, zonas y carga de imágenes.

Para desarrollo se usa SQLite y se crean datos iniciales automáticamente. En producción usa PostgreSQL porque el sistema de archivos de una función no es almacenamiento persistente.

## Despliegue en Vercel

El entrypoint ASGI está declarado en `pyproject.toml`. Importa esta carpeta como un proyecto separado, agrega las variables anteriores y despliega. Conecta un Blob Store público desde Storage; Vercel añadirá el token al proyecto.
