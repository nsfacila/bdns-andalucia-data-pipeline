"""
Orquestador principal de Grant Hunter AI (captador_bbdd/main.py).
Ejecuta el ciclo de vida completo:
1. Consulta la API de la BDNS para CDTI y Red.es.
2. Aplica filtros de fecha, organismo y programa; deja pendiente la ubicación no enriquecida.
3. Guarda la traza completa de auditoría en un CSV histórico local.
4. Desduplica y sincroniza con Notion solo las subvenciones aceptadas.
"""

import csv
import logging
import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional

# Garantizar que el paquete captador_bbdd sea localizable
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from captador_bbdd.conectores_bdns import obtener_todas_las_concesiones
from captador_bbdd.enriquecimiento_territorial import (
    EnriquecimientoConfigurationError,
    anotar_pendiente,
    enriquecer_concesiones,
)
from captador_bbdd.filtros import evaluar_subvencion
from captador_bbdd.notion import ClienteNotion

# Configuración de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# Configuración de rutas y columnas para el CSV local (fuente para Power BI)
PATH_CSV_HISTORICO = os.path.join("data", "registro_historico.csv")
CAMPOS_CSV = [
    "fecha_captura", "id_concesion", "empresa", "cif", "tipo_empresa", 
    "sector", "subsector_cnae", "numero_trabajadores", "web",
    "comunidad_autonoma", "provincia", "ciudad", "delegaciones", "presencia_andalucia",
    "domicilio_social", "codigo_postal", "municipio", "clasificacion_territorial",
    "nivel_confianza", "estado_verificacion", "fuente_verificacion",
    "referencia_verificacion", "fecha_verificacion", "fecha_dato_fuente",
    "motivo_enriquecimiento", "empresa_registral",
    "organismo", "convocatoria", "proyecto", "expediente", "area_tematica", 
    "duracion", "mercado_eurecat", "fecha_concesion", "presupuesto", "subvencion", 
    "estado_evaluacion", "motivo_desestimacion"
]


def inicializar_csv_historico() -> None:
    """Crea el histórico o amplía sus cabeceras conservando las filas existentes."""
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(PATH_CSV_HISTORICO) or os.path.getsize(PATH_CSV_HISTORICO) == 0:
        with open(PATH_CSV_HISTORICO, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CAMPOS_CSV)
            writer.writeheader()
        return

    with open(PATH_CSV_HISTORICO, mode="r", newline="", encoding="utf-8-sig") as f:
        lector = csv.DictReader(f)
        cabeceras_existentes = lector.fieldnames or []
        if not cabeceras_existentes:
            raise RuntimeError(
                f"El histórico {PATH_CSV_HISTORICO} no contiene cabeceras válidas."
            )
        cabeceras_nuevas = list(dict.fromkeys(cabeceras_existentes + CAMPOS_CSV))
        if cabeceras_nuevas == cabeceras_existentes:
            CAMPOS_CSV[:] = cabeceras_nuevas
            return
        filas_existentes = list(lector)

    temporal = f"{PATH_CSV_HISTORICO}.tmp"
    with open(temporal, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=cabeceras_nuevas)
        writer.writeheader()
        writer.writerows(filas_existentes)
    os.replace(temporal, PATH_CSV_HISTORICO)
    CAMPOS_CSV[:] = cabeceras_nuevas


def _clave_concesion(concesion: Dict[str, Any]) -> Optional[str]:
    """Genera una clave por ayuda, evitando descartar otras ayudas del mismo CIF."""
    identificador = str(concesion.get("id_concesion") or concesion.get("expediente") or "").strip()
    if identificador:
        return f"id:{identificador}"

    cif = str(concesion.get("cif") or "").strip().upper()
    convocatoria = str(concesion.get("convocatoria") or "").strip().casefold()
    proyecto = str(concesion.get("proyecto") or "").strip().casefold()
    if cif and (convocatoria or proyecto):
        return f"fallback:{cif}:{convocatoria}:{proyecto}"
    return None


def registrar_fila_csv(registro: Dict[str, Any], dictamen: Dict[str, Any]) -> None:
    """Registra cada concesión analizada en el archivo CSV histórico."""
    with open(PATH_CSV_HISTORICO, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPOS_CSV)
        writer.writerow({
            "fecha_captura": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "id_concesion": registro.get("id_concesion", ""),
            "empresa": registro.get("empresa", ""),
            "cif": registro.get("cif", ""),
            "tipo_empresa": registro.get("tipo_empresa", ""),
            "sector": registro.get("sector", ""),
            "subsector_cnae": registro.get("subsector_cnae", ""),
            "numero_trabajadores": registro.get("numero_trabajadores", ""),
            "web": registro.get("web", ""),
            "comunidad_autonoma": registro.get("comunidad_autonoma", ""),
            "provincia": registro.get("provincia", ""),
            "ciudad": registro.get("ciudad", ""),
            "delegaciones": registro.get("delegaciones", ""),
            "presencia_andalucia": registro.get("presencia_andalucia", ""),
            "domicilio_social": registro.get("domicilio_social", ""),
            "codigo_postal": registro.get("codigo_postal", ""),
            "municipio": registro.get("municipio", ""),
            "clasificacion_territorial": registro.get("clasificacion_territorial", ""),
            "nivel_confianza": registro.get("nivel_confianza", ""),
            "estado_verificacion": registro.get("estado_verificacion", ""),
            "fuente_verificacion": registro.get("fuente_verificacion", ""),
            "referencia_verificacion": registro.get("referencia_verificacion", ""),
            "fecha_verificacion": registro.get("fecha_verificacion", ""),
            "fecha_dato_fuente": registro.get("fecha_dato_fuente", ""),
            "motivo_enriquecimiento": registro.get("motivo_enriquecimiento", ""),
            "empresa_registral": registro.get("empresa_registral", ""),
            "organismo": registro.get("organismo", ""),
            "convocatoria": registro.get("convocatoria", ""),
            "proyecto": registro.get("proyecto", ""),
            "expediente": registro.get("expediente", ""),
            "area_tematica": registro.get("area_tematica", ""),
            "duracion": registro.get("duracion", ""),
            "mercado_eurecat": registro.get("mercado_eurecat", ""),
            "fecha_concesion": registro.get("fecha_concesion", ""),
            "presupuesto": registro.get("presupuesto", 0.0),
            "subvencion": registro.get("subvencion", 0.0),
            "estado_evaluacion": dictamen.get("estado", ""),
            "motivo_desestimacion": dictamen.get("motivo", "")
        })


def ejecutar_pipeline(
    dry_run: bool = False,
    enriquecer: Optional[bool] = None,
) -> None:
    """
    Ejecuta el pipeline completo de captación.
    - dry_run=True: Ejecuta la consulta y el filtro registrando en el CSV, pero NO envía a Notion.
    - enriquecer=None: Enriquece en ejecuciones reales; en dry-run no consume llamadas de API.
    """
    log.info("=== INICIANDO PIPELINE GRANT HUNTER AI ===")
    inicializar_csv_historico()

    # Cargar variables de entorno para la API de Notion
    token_notion = os.getenv("NOTION_TOKEN")
    database_id_notion = os.getenv("NOTION_DATABASE_ID")

    cliente_notion = None
    claves_registradas = set()

    # Conectar con Notion si no es un simulacro
    if not dry_run and (not token_notion or not database_id_notion):
        raise RuntimeError("NOTION_TOKEN y NOTION_DATABASE_ID son obligatorios si DRY_RUN está desactivado.")
    if not dry_run:
        try:
            cliente_notion = ClienteNotion(token=token_notion, database_id=database_id_notion)
            subvenciones_existentes = cliente_notion.leer_subvenciones_existentes()
            claves_registradas = {
                clave
                for s in subvenciones_existentes
                if (clave := _clave_concesion(s)) is not None
            }
            log.info(f"[Notion] {len(claves_registradas)} concesiones cargadas para desduplicación.")
        except Exception as e:
            log.error(f"[Notion Error] No se pudo leer la base de datos: {e}")
            raise
    else:
        log.info("[Notion] Modo simulación (DRY_RUN) activo.")

    # 1. Extracción de datos desde el conector BDNS
    concesiones_raw = obtener_todas_las_concesiones()
    log.info(f"Total de concesiones descargadas desde BDNS: {len(concesiones_raw)}")
    ejecutar_enriquecimiento = not dry_run if enriquecer is None else enriquecer
    if ejecutar_enriquecimiento:
        try:
            max_consultas = int(os.getenv("ENRIQUECIMIENTO_MAX_CONSULTAS", "100"))
            concesiones_raw = enriquecer_concesiones(
                concesiones_raw,
                max_consultas=max_consultas,
            )
        except EnriquecimientoConfigurationError as error:
            log.error(
                "No se pudo ejecutar el enriquecimiento territorial: %s. "
                "Las concesiones sin datos verificados seguirán pendientes.",
                error,
            )
            concesiones_raw = [
                anotar_pendiente(concesion, str(error))
                for concesion in concesiones_raw
            ]
    else:
        log.info("Enriquecimiento territorial desactivado; no se consumirán consultas.")
        concesiones_raw = [
            anotar_pendiente(
                concesion,
                "Enriquecimiento territorial desactivado para esta ejecución.",
            )
            for concesion in concesiones_raw
        ]

    aceptadas_count = 0
    descartadas_count = 0
    pendientes_enriquecimiento_count = 0
    duplicadas_count = 0
    errores_notion = 0

    # 2. Evaluación, Filtrado y Sincronización
    for conc in concesiones_raw:
        dictamen = evaluar_subvencion(conc)
        
        # Registrar siempre la traza en el CSV histórico
        registrar_fila_csv(conc, dictamen)

        if dictamen["estado"] == "aceptada":
            clave = _clave_concesion(conc)
            
            # Evitar duplicar la misma concesión, no otras ayudas del mismo CIF.
            if clave and clave in claves_registradas:
                duplicadas_count += 1
                continue

            aceptadas_count += 1
            log.info(f" [ACEPTADA] {conc['empresa']} ({conc['provincia']}) -> {conc['convocatoria']}")

            # Insertar en Notion
            if cliente_notion and not dry_run:
                try:
                    cliente_notion.crear_subvencion(conc)
                    if clave:
                        claves_registradas.add(clave)
                except Exception as e:
                    errores_notion += 1
                    log.error(f" [Notion Error] Falló al insertar la empresa {conc['empresa']}: {e}")
            elif clave:
                claves_registradas.add(clave)
        elif dictamen["estado"] == "pendiente_enriquecimiento":
            pendientes_enriquecimiento_count += 1
            log.info(
                " [PENDIENTE] %s: %s",
                conc.get("empresa") or "Beneficiario sin identificar",
                dictamen["motivo"],
            )
        else:
            descartadas_count += 1

    # Resumen final de la ejecución
    log.info("\n" + "="*60)
    log.info(" RESUMEN DE LA EJECUCIÓN DEL CAPTADOR")
    log.info("="*60)
    log.info(f" Total procesadas:         {len(concesiones_raw)}")
    log.info(f" Aceptadas (Nuevas):       {aceptadas_count}")
    log.info(f" Duplicadas omitidas:      {duplicadas_count}")
    log.info(f" Pendientes enriquecimiento: {pendientes_enriquecimiento_count}")
    log.info(f" Descartadas:              {descartadas_count}")
    log.info(f" Histórico acumulado en:  {PATH_CSV_HISTORICO}")
    log.info("="*60 + "\n")
    if errores_notion:
        raise RuntimeError(f"No se pudieron sincronizar {errores_notion} concesiones con Notion.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    # Si DRY_RUN es True en el .env, no realiza cambios en Notion
    dry_run_env = os.getenv("DRY_RUN", "False").lower() in ("true", "1", "yes")
    ejecutar_pipeline(dry_run=dry_run_env)