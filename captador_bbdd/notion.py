"""
Módulo para la lectura, desduplicación y escritura de subvenciones 
en la base de datos de Notion mediante la API REST oficial.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Any, List, Optional
import requests

log = logging.getLogger(__name__)

API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _extraer_texto(prop: Optional[Dict[str, Any]]) -> str:
    """Extrae texto plano de cualquier tipo de propiedad devuelta por Notion."""
    if not prop:
        return ""
    tipo = prop.get("type")
    valor = prop.get(tipo)
    if tipo in ("title", "rich_text"):
        return "".join(t.get("plain_text", "") for t in valor or [])
    if tipo == "select":
        return (valor or {}).get("name", "")
    if tipo in ("url", "email", "phone_number"):
        return valor or ""
    return ""


class ClienteNotion:
    """Cliente para gestionar la interacción con la base de datos de Subvenciones en Notion."""
    
    def __init__(self, token: str, database_id: str):
        self.database_id = database_id
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        })

    def _post(self, ruta: str, cuerpo: dict) -> dict:
        """Realiza peticiones POST a la API de Notion con reintentos para evitar límites de tasa (429)."""
        for intento in range(3):
            r = self.session.post(f"{API_URL}{ruta}", json=cuerpo, timeout=30)
            if r.status_code == 429:  # Control de límite de peticiones de Notion (~3 por segundo)
                tiempo_espera = float(r.headers.get("Retry-After", 1)) * (intento + 1)
                time.sleep(tiempo_espera)
                continue
            if not r.ok:
                raise RuntimeError(f"Error Notion {r.status_code}: {r.text[:300]}")
            return r.json()
        raise RuntimeError("Notion: Límite de peticiones excedido tras varios reintentos.")

    def leer_subvenciones_existentes(self) -> List[Dict[str, str]]:
        """
        Lee todas las concesiones registradas en Notion (con paginación).
        Devuelve una lista con los identificadores clave (CIF e ID Concesión/Expediente) para desduplicar.
        """
        registros, cursor = [], None
        while True:
            cuerpo = {"page_size": 100}
            if cursor:
                cuerpo["start_cursor"] = cursor
            
            datos = self._post(f"/databases/{self.database_id}/query", cuerpo)
            for p in datos.get("results", []):
                props = p.get("properties", {})
                registros.append({
                    "cif": _extraer_texto(props.get("CIF")),
                    "empresa": _extraer_texto(props.get("Empresa")),
                    "id_concesion": _extraer_texto(props.get("ID Concesión")),
                    "expediente": _extraer_texto(props.get("Expediente")),
                    "convocatoria": _extraer_texto(props.get("Programa de la Subvención")),
                    "proyecto": _extraer_texto(props.get("Proyecto"))
                })
            
            if not datos.get("has_more"):
                return registros
            cursor = datos.get("next_cursor")

    def crear_subvencion(self, sub: Dict[str, Any]) -> None:
        """
        Crea una nueva página/fila en la base de datos de Notion mapeando
        el esquema completo de datos de la empresa y la subvención.
        """
        def prop_texto(v: Any) -> Dict[str, Any]:
            val_str = str(v) if v is not None and str(v).lower() != "nan" else ""
            return {"rich_text": [{"text": {"content": val_str[:2000]}}]}

        def prop_select(
            v: Any,
            valor_defecto: Optional[str] = "Sin especificar",
        ) -> Dict[str, Any]:
            val_str = (
                str(v).strip()
                if v is not None and str(v).lower() != "nan" and str(v).strip()
                else valor_defecto
            )
            if val_str is None:
                return {"select": None}
            # Reemplazar comas o saltos de línea para evitar errores de select en Notion
            val_clean = val_str.replace("\n", " ").strip()[:100]
            return {"select": {"name": val_clean}}

        # Mapeo de propiedades a los tipos de Notion
        props: Dict[str, Any] = {
            # --- Datos Principales de la Empresa ---
            "Empresa": {
                "title": [{
                    "text": {
                        "content": str(
                            sub.get("empresa_registral") or sub.get("empresa") or "Sin Nombre"
                        )[:2000]
                    }
                }]
            },
            "CIF": prop_texto(sub.get("cif", sub.get("nif", ""))),
            "Tipo de empresa": prop_select(sub.get("tipo_empresa"), "Sin especificar"),
            "Sector": prop_texto(sub.get("sector", "Sin especificar")),
            "Subsector CNAE": prop_texto(sub.get("subsector_cnae", "")),
            "Número de trabajadores": prop_texto(sub.get("numero_trabajadores", "")),
            
            # --- Ubicación y Delegaciones ---
            "Comunidad Autónoma": prop_select(sub.get("comunidad_autonoma"), None),
            "Provincia": prop_texto(sub.get("provincia", "")),
            "Ciudad": prop_texto(sub.get("municipio") or sub.get("ciudad", "")),
            "Delegaciones": prop_texto(sub.get("delegaciones", "")),
            "Presencia en Andalucía": prop_select(sub.get("presencia_andalucia"), None),

            # --- Convocatoria y Proyecto ---
            "Organo concedente": prop_select(sub.get("organismo"), "CDTI"),
            "Programa de la Subvención": prop_texto(sub.get("convocatoria", sub.get("programa", ""))),
            "Proyecto": prop_texto(sub.get("proyecto", "Sin título de proyecto")),
            "Expediente": prop_texto(sub.get("expediente", sub.get("id_concesion", ""))),
            "Área temática": prop_texto(sub.get("area_tematica", "")),
            "Duración": prop_texto(sub.get("duracion", "")),
            "Mercado Eurecat": prop_texto(sub.get("mercado_eurecat", "")),
            "Año convocatoria": prop_texto(sub.get("fecha_concesion", "2024")),

            # --- Económicos y Estado ---
            "Presupuesto (€)": {"number": float(sub.get("presupuesto", 0.0))},
            "Subvención (€)": {"number": float(sub.get("subvencion", 0.0))},
            "Estado": prop_select(sub.get("estado_concesion", sub.get("estado")), "Estimada"),
            "Motivo de la desestimación": prop_texto(sub.get("motivo_desestimacion", "")),
            "ID Concesión": prop_texto(sub.get("id_concesion", sub.get("expediente", "")))
        }

        # Manejo opcional de campo URL
        web = sub.get("web")
        if web and str(web).startswith("http"):
            props["web"] = {"url": str(web)}
        elif web and isinstance(web, str) and len(web) > 3:
            props["web"] = {"url": f"https://{web}"}

        cuerpo_peticion = {
            "parent": {"database_id": self.database_id},
            "properties": props
        }

        self._post("/pages", cuerpo_peticion)
        time.sleep(0.35)  # Margen para respetar el límite de tasa de la API de Notion