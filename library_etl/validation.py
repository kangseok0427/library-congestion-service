"""Upload notices requiring administrator action; source diagnostics stay internal."""
import logging
from collections import Counter

# Hourly source values remain authoritative; latest-date uncertainty remains
# marked partial and excluded from forecasting. Neither needs a user action.
INTERNAL_CODES = frozenset({'HOURLY_TOTAL_MISMATCH', 'PARTIAL_DATE_UNCONFIRMED'})
logger = logging.getLogger(__name__)


def public_validation(report):
    diagnostics = report.get('warnings', [])
    counts = Counter(w['code'] for w in diagnostics if w['code'] in INTERNAL_CODES)
    if counts:
        logger.info('Excel reference diagnostics handled internally: %s', dict(counts))
    actionable = [w for w in diagnostics if w['code'] not in INTERNAL_CODES]
    warnings = [{'code': w['code'], 'message': w.get('message', 'Excel 검증 결과를 확인하세요.'),
                 **({'row': w['row']} if 'row' in w else {})} for w in actionable[:100]]
    return {'warning_count': len(actionable), 'warnings': warnings}
