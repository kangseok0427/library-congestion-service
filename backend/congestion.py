"""
T04 혼잡도 판정 기준 (ADE-8)

기준: 과거 동일 시간대 방문량 분포에서의 midrank 백분위 점수
  - 35 이하 → 여유 (quiet)
  - 36 ~ 70 → 보통 (normal)
  - 71 이상  → 혼잡 (busy)

좌석 점유율·현재 체류인원을 추정하지 않습니다.
분포 전체가 동일한 값이면 50.0(보통)으로 처리합니다.
"""

# 단계 경계 (백분위). T04 검수 체크리스트 통과 후 변경하세요.
THRESHOLDS: dict[str, float] = {
    'quiet': 35.0,   # 35 이하: 여유
    'normal': 70.0,  # 70 이하: 보통, 70 초과: 혼잡
}

# 이용자에게 표시할 레이블
LABELS: dict[str, str] = {
    'quiet': '여유',
    'normal': '보통',
    'busy': '혼잡',
}

# 각 단계의 안내 문구
DESCRIPTIONS: dict[str, str] = {
    'quiet': '과거 동일 시간대보다 방문량이 적을 것으로 예상됩니다.',
    'normal': '과거 동일 시간대와 비슷한 방문량이 예상됩니다.',
    'busy': '과거 동일 시간대보다 방문량이 많을 것으로 예상됩니다.',
}


def classify(score: float) -> str:
    """백분위 점수 → 혼잡도 단계 키('quiet' | 'normal' | 'busy')."""
    if score <= THRESHOLDS['quiet']:
        return 'quiet'
    if score <= THRESHOLDS['normal']:
        return 'normal'
    return 'busy'


def midrank_score(value: float | int, distribution: list) -> tuple[float, str]:
    """
    분포 내 값의 midrank 백분위 점수와 혼잡도 단계를 반환합니다.

    midrank 공식: (분포에서 value보다 작은 수 + 0.5 × 같은 수) / 전체 수 × 100
    분포 전체가 동일한 값이면 50.0 / 'normal'을 반환합니다.

    Args:
        value:        판정할 값 (예: 예상 방문자 수)
        distribution: 비교 기준 분포 (과거 동일 시간대 관측값 목록, 비어 있으면 안 됨)

    Returns:
        (score, level): 백분위 점수(소수점 1자리)와 혼잡도 단계 키

    Raises:
        ValueError: distribution이 비어 있을 때
    """
    if not distribution:
        raise ValueError('분포가 비어 있습니다.')
    n = len(distribution)
    below = sum(v < value for v in distribution)
    equal = sum(v == value for v in distribution)
    score = round(100.0 * (below + 0.5 * equal) / n, 1)
    return score, classify(score)


def label(level: str) -> str:
    """혼잡도 단계 키 → 이용자 표시 레이블. 알 수 없는 단계는 '알 수 없음'."""
    return LABELS.get(level, '알 수 없음')


def description(level: str) -> str:
    """혼잡도 단계 키 → 안내 문구. 알 수 없는 단계는 빈 문자열."""
    return DESCRIPTIONS.get(level, '')


def criteria_info() -> dict:
    """혼잡도 판정 기준 요약 (API /meta 등에서 활용)."""
    return {
        'basis': '과거 동일 시간대 방문량 분포의 midrank 백분위',
        'note': '좌석 점유율·현재 체류인원을 추정하지 않습니다.',
        'thresholds': {
            'quiet': f'백분위 {THRESHOLDS["quiet"]:.0f} 이하',
            'normal': f'백분위 {THRESHOLDS["normal"]:.0f} 이하',
            'busy': f'백분위 {THRESHOLDS["normal"]:.0f} 초과',
        },
        'labels': LABELS,
        'descriptions': DESCRIPTIONS,
    }
