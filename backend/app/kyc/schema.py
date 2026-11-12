from pydantic import BaseModel, Field

# fields read from an identity document; the first four decide agreement
CRITICAL_FIELDS = ("full_name", "document_number", "date_of_birth", "expiry_date")
FIELD_NAMES = (
    "doc_type",
    "issuing_country",
    "document_number",
    "surname",
    "given_names",
    "full_name",
    "date_of_birth",
    "expiry_date",
    "sex",
    "nationality",
    "mrz",
)

DOC_TYPES = ("national_id", "passport", "drivers_licence")


class IdFields(BaseModel):
    """Strict schema the VLM must fill. Every value is copied verbatim from the document."""

    doc_type: str | None = Field(
        None, description="national_id | passport | drivers_licence | other"
    )
    issuing_country: str | None = Field(
        None, description="Issuing country, as the 3-letter ISO code if printed, e.g. ZWE, KEN"
    )
    document_number: str | None = Field(
        None, description="ID / passport / licence number exactly as printed"
    )
    surname: str | None = Field(None, description="Surname / family name as printed")
    given_names: str | None = Field(None, description="Given names / first names as printed")
    full_name: str | None = Field(
        None, description="Full name as printed when surname and given names are not separate"
    )
    date_of_birth: str | None = Field(None, description="Date of birth as printed")
    expiry_date: str | None = Field(None, description="Date of expiry as printed")
    sex: str | None = Field(None, description="Sex as printed: M or F")
    nationality: str | None = Field(None, description="Nationality as printed")
    mrz: str | None = Field(
        None,
        description="The machine-readable zone lines at the bottom of passports and some ID "
        "cards (rows of capital letters, digits and < characters), one per line; null if absent",
    )


def id_json_schema() -> dict:
    return IdFields.model_json_schema()
