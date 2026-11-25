# Cambios

## [0.1.0] - 2026-11-25

### Agregado

- Consultas en paralelo con `asyncio` y `httpx`, con tope de concurrencia y reintentos.
- Precios desde páginas HTML (selector CSS) y APIs JSON (ruta), con formatos chilenos e internacionales.
- Páginas con JavaScript mediante Playwright (extra opcional `js`).
- Respeto de `robots.txt` y `Crawl-delay`, y pausa mínima entre consultas al mismo sitio.
- Historial en SQLite con migraciones y alertas por Telegram (umbral y baja porcentual).
- CLI `monitor` con los comandos `revisar`, `resumen`, `historial`, `grafico`, `exportar` y `validar`.
- Ejecución cada 6 horas en GitHub Actions, con el historial publicado en la rama `datos`.
