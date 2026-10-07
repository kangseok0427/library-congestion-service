"""Suppress known reference diagnostics only; never drop unknown actionable warnings."""
import logging

from library_etl.validation import public_validation


def test_reference_notices_are_logged_but_not_shown(caplog):
    report = {'warnings': [{'code': 'HOURLY_TOTAL_MISMATCH', 'row': 2}] * 3626
              + [{'code': 'PARTIAL_DATE_UNCONFIRMED'}]}
    with caplog.at_level(logging.INFO, logger='library_etl.validation'):
        assert public_validation(report) == {'warning_count': 0, 'warnings': []}
    assert '3626' in caplog.text and 'PARTIAL_DATE_UNCONFIRMED' in caplog.text
    assert len(report['warnings']) == 3627


def test_unknown_notices_remain_actionable_and_total_is_not_capped():
    report = {'warnings': [{'code': 'NEEDS_REVIEW', 'row': n, 'message': '확인하세요.'}
                           for n in range(150)] + [{'code': 'HOURLY_TOTAL_MISMATCH'}]}
    result = public_validation(report)
    assert result['warning_count'] == 150 and len(result['warnings']) == 100
    assert result['warnings'][0] == {'code': 'NEEDS_REVIEW', 'row': 0, 'message': '확인하세요.'}
