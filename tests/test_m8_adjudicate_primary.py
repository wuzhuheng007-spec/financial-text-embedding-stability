import pytest

from scripts.m8_adjudicate_primary import PREFIX, SUFFIX, prove


SCRIPT = b'<script type="text/javascript" src="/x"></script>'


def test_exact_wrapper_and_one_prior_script_passes(monkeypatch):
    # The production check requires the observed 114-byte script length.
    padded = b'<script type="text/javascript"  src="/' + b'x' * 65 + b'"></script>'
    assert len(padded) == 114
    inner = b'<html><body>body</body></html>'
    prior = inner.replace(b'</body>', padded + b'</body>')
    result = prove(prior, PREFIX + inner + SUFFIX)
    assert result['exact_unwrapped_bytes_equal'] is True
    assert result['script_bytes'] == 114


def test_substantive_body_difference_rejected():
    padded = b'<script type="text/javascript"  src="/' + b'x' * 65 + b'"></script>'
    prior = b'<html><body>old' + padded + b'</body></html>'
    with pytest.raises(ValueError):
        prove(prior, PREFIX + b'<html><body>new</body></html>' + SUFFIX)


def test_missing_wrapper_rejected():
    with pytest.raises(ValueError):
        prove(b'<html></html>', b'<html></html>')
