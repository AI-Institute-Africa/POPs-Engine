"""Reader for documents that carry their own digital text (PDF text layers, Word documents).

Digital text is exact: there is no character recognition to get wrong, so its readings carry
high confidence and count as an independent reader next to the vision model. Whether the
document itself is genuine is a separate question for the fraud checks; digital documents are
easy to edit, and the risk engine weighs that.
"""

from app.extraction import templates
from app.extraction.schema import FieldReading

TEXT_CONFIDENCE = 0.98


class TextLayerReader:
    name = "text_layer"
    version = "text-layer:1"

    def __init__(self, text: str):
        self.text = text

    def read_text(self) -> dict[str, FieldReading]:
        return {
            name: FieldReading(name, value, TEXT_CONFIDENCE)
            for name, value in templates.parse_text(self.text).items()
        }
