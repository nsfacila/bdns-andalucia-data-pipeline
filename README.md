
# BDNS Subsidy Data Enrichment

Pipeline de extracción y enriquecimiento de datos de subvenciones públicas a partir de la Base de Datos Nacional de Subvenciones (BDNS).

## Objetivo

Identificar empresas beneficiarias de subvenciones públicas y determinar su domicilio social para detectar aquellas domiciliadas en Andalucía.

## Flujo de trabajo

1. **Extracción:** recopilación de concesiones de subvenciones desde BDNS.
2. **Normalización:** limpieza y estructuración de los datos.
3. **Enriquecimiento territorial:** búsqueda del domicilio social de las empresas a partir de su CIF.
4. **Verificación:** clasificación de los resultados según la fiabilidad de la identificación y del domicilio.
5. **Filtrado:** identificación de empresas con domicilio social en Andalucía.

## Estados de verificación

| Estado | Descripción |
|---|---|
| VERIFICADO | Empresa identificada y domicilio contrastado. |
| PENDIENTE | Empresa identificada, domicilio sin confirmar. |
| NO_IDENTIFICADO | Empresa no identificada con suficiente seguridad. |

## Tecnologías

- Python
- Pandas
- APIs y fuentes de datos públicas
- Git y GitHub
- Notion (integración prevista)

## Calidad y trazabilidad

El proyecto conserva la fuente y la fecha de verificación de los domicilios. No se deduce la ubicación geográfica a partir del CIF.

## Estado del proyecto

- [x] Extracción de concesiones BDNS.
- [x] Exportación de resultados a CSV.
- [ ] Enriquecimiento territorial por CIF.
- [ ] Verificación de domicilios sociales.
- [ ] Filtrado de empresas domiciliadas en Andalucía.
- [ ] Integración con Notion.
