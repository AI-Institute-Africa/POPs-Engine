from app.claims.facts import FieldView, too_many_low_confidence


def _f(agreed: bool, corrected=False, value="x"):
    return FieldView(value, agreed, corrected)


def test_rule_off_by_default():
    assert not too_many_low_confidence({"a": _f(False), "b": _f(False)}, None)


def test_counts_only_unagreed_uncorrected_read_fields():
    fields = {
        "a": _f(False),
        "b": _f(False),
        "c": _f(True),
        "d": _f(False, corrected=True),
        "e": _f(False, value=None),
    }
    assert too_many_low_confidence(fields, 1)
    assert not too_many_low_confidence(fields, 2)
