from typing import Protocol

from PIL import Image

from app.extraction.schema import FieldReading


class ReaderUnavailable(Exception):
    """The reader cannot run (service down, binary missing). The pipeline fails closed."""


class Reader(Protocol):
    name: str
    # True: run on every page and merge; False: the reader reads the whole file at once
    per_page: bool

    @property
    def version(self) -> str: ...

    def read(self, image: Image.Image, raw: bytes) -> dict[str, FieldReading]: ...
