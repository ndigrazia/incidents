from pydantic import BaseModel, Field
from typing import Optional, List

class Incident(BaseModel):
    """Pydantic model representing a single Remedy incident."""
    resumen: Optional[str] = Field(None, alias="Resumen")
    notes: Optional[str] = Field(None, alias="Notas")
    estado: Optional[str] = Field(None, alias="Estado")
    resolucion: Optional[str] = Field(None, alias="Resolucion")
    grupo_propietario: Optional[str] = Field(None, alias="Grupo_Propietario")
    numero_incidente: Optional[str] = Field(None, alias="Numero_Incidente")
    fecha_creacion_reg: Optional[str] = Field(None, alias="FechaCreacionREG")
    vendor_ticket_number: Optional[str] = Field(None, alias="Vendor_Ticket_Number")
    ci: Optional[str] = Field(None, alias="CI")
    ci_vendor: Optional[str] = Field(None, alias="CI_Vendor")
    reported_date: Optional[str] = Field(None, alias="Reported_Date")
    grupo_asignado: Optional[str] = Field(None, alias="Grupo_Asignado")

    model_config = {
        "populate_by_name": True
    }


class RemedySOAPResponse(BaseModel):
    """Pydantic model representing the parsed list of incidents from the SOAP response."""
    incidents: List[Incident] = Field(default_factory=list, description="List of parsed incident objects.")
