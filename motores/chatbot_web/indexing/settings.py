"""Configuration used only by the local indexer."""

from pydantic import Field, model_validator
from config import Settings as ChatSettings


class Settings(ChatSettings):
    seeds: list[str] = Field(
        default_factory=lambda: [
            "https://www.unamba.edu.pe/",
            "https://www.gob.pe/universidad-nacional-micaela-bastidas-de-apurimac",
            "https://www.transparencia.gob.pe/enlaces/pte_transparencia_enlaces.aspx?id_entidad=13625",
            "https://admision.unamba.edu.pe/",
            "https://biblioteca.unamba.edu.pe/",
            "https://centrodeinformatica.unamba.edu.pe/",
            "https://comedor.unamba.edu.pe/",
            "https://convenios.unamba.edu.pe/",
            "https://cpu.unamba.edu.pe/",
            "https://idiomas.unamba.edu.pe/",
            "https://revistas.unamba.edu.pe/",
            "https://sisacademico.unamba.edu.pe/",
            "https://vrin.unamba.edu.pe/",
        ]
    )

    allowed_domains: list[str] = Field(
        default_factory=lambda: [
            "www.unamba.edu.pe",
            "unamba.edu.pe",
            "www.gob.pe",
            "www.transparencia.gob.pe",
            "admision.unamba.edu.pe",
            "biblioteca.unamba.edu.pe",
            "centrodeinformatica.unamba.edu.pe",
            "comedor.unamba.edu.pe",
            "convenios.unamba.edu.pe",
            "cpu.unamba.edu.pe",
            "idiomas.unamba.edu.pe",
            "revistas.unamba.edu.pe",
            "sisacademico.unamba.edu.pe",
            "vrin.unamba.edu.pe",
        ]
    )

    shared_scopes: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "www.gob.pe": [
                "/universidad-nacional-micaela-bastidas-de-apurimac",
                "/unamba",
                "/institucion/unamba",
                "/institucion/universidad-nacional-micaela-bastidas-de-apurimac",
                "/institucion/344",
                "/104314",
                "/104377",
                "/116489",
                "/13070",
                "/61171",
                "/104314-universidad-nacional-micaela-bastidas-de-apurimac-acceder-al-repositorio-institucional-de-la-universidad",
                "/104377-acceder-a-la-biblioteca-de-la-unamba",
                "/116489-convocatorias-y-servicios-unamba",
                "/13070-universidad-nacional-micaela-bastidas-de-apurimac-organizacion-de-universidad-nacional-micaela-bastidas-de-apurimac",
                "/61171-universidad-nacional-micaela-bastidas-de-apurimac-consultar-informacion-de-transparencia",
            ],
            "www.transparencia.gob.pe": ["/enlaces/"],
        }
    )

    max_pages: int = Field(2000, ge=1)

    max_depth: int = Field(8, ge=0)

    max_seconds: int = Field(3600, ge=1)

    max_response_bytes: int = Field(2097152, ge=1024)

    crawl_concurrency: int = Field(3, ge=1, le=32)

    domain_delay: float = Field(1, ge=0.1)

    max_retries: int = Field(3, ge=0, le=8)

    request_timeout: float = Field(25, gt=0)

    embed_batch_size: int = Field(16, ge=1)

    chunk_size: int = Field(900, ge=200)

    chunk_overlap: int = Field(120, ge=0)

    javascript_enabled: bool = False

    @model_validator(mode="after")
    def validate_chunking(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be less than CHUNK_SIZE")
        return self
