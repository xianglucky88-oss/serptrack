import pytest

def test_rankstore_is_open_gap():
    from src.serptrack import rankstore
    with pytest.raises(NotImplementedError):
        rankstore.process({"id": "x"})

def test_geo_locale_is_open_gap():
    from src.serptrack import geo_locale
    with pytest.raises(NotImplementedError):
        geo_locale.process({"id": "x"})
