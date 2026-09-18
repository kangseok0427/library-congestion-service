"""T04 혼잡도 판정 기준 단위 테스트 (ADE-8)."""
import pytest
from backend.congestion import (
    LABELS, THRESHOLDS,
    classify, criteria_info, description, label, midrank_score,
)


class TestClassify:
    def test_quiet_lower(self):
        assert classify(0.0) == 'quiet'

    def test_quiet_boundary(self):
        assert classify(THRESHOLDS['quiet']) == 'quiet'

    def test_normal_just_above_quiet(self):
        assert classify(THRESHOLDS['quiet'] + 0.1) == 'normal'

    def test_normal_boundary(self):
        assert classify(THRESHOLDS['normal']) == 'normal'

    def test_busy_just_above_normal(self):
        assert classify(THRESHOLDS['normal'] + 0.1) == 'busy'

    def test_busy_upper(self):
        assert classify(100.0) == 'busy'


class TestMidrankScore:
    def test_empty_distribution_raises(self):
        with pytest.raises(ValueError):
            midrank_score(10, [])

    def test_constant_distribution_is_normal(self):
        # 분포 전체가 같은 값이면 '보통'
        _, level = midrank_score(5, [5, 5, 5, 5])
        assert level == 'normal'

    def test_below_all_is_quiet(self):
        score, level = midrank_score(0, [10, 20, 30])
        assert score == 0.0
        assert level == 'quiet'

    def test_above_all_is_busy(self):
        score, level = midrank_score(100, [10, 20, 30])
        assert score == 100.0
        assert level == 'busy'

    def test_exact_quiet_boundary(self):
        # 분포 [0..99]에서 value=35이면 백분위 ≈ 35.5 → normal
        dist = list(range(100))
        score, level = midrank_score(35, dist)
        assert score == pytest.approx(35.5, abs=0.1)
        assert level == 'normal'

    def test_returns_tuple_of_two(self):
        result = midrank_score(50, [10, 50, 90])
        assert len(result) == 2

    def test_score_is_float(self):
        score, _ = midrank_score(10, [5, 10, 15])
        assert isinstance(score, float)

    def test_single_element_distribution(self):
        # 원소가 1개인 분포: value == 원소이면 midrank = 50
        score, level = midrank_score(7, [7])
        assert score == 50.0
        assert level == 'normal'

    def test_value_below_single_element(self):
        score, level = midrank_score(0, [7])
        assert score == 0.0
        assert level == 'quiet'


class TestLabel:
    def test_quiet(self):
        assert label('quiet') == '여유'

    def test_normal(self):
        assert label('normal') == '보통'

    def test_busy(self):
        assert label('busy') == '혼잡'

    def test_unknown(self):
        assert label('unknown') == '알 수 없음'

    def test_labels_match_constant(self):
        for k, v in LABELS.items():
            assert label(k) == v


class TestDescription:
    def test_all_levels_nonempty(self):
        for level in ('quiet', 'normal', 'busy'):
            assert description(level), f'{level} 설명이 비어 있습니다.'

    def test_unknown_is_empty_string(self):
        assert description('bad') == ''


class TestCriteriaInfo:
    def test_required_keys(self):
        info = criteria_info()
        for key in ('basis', 'note', 'thresholds', 'labels', 'descriptions'):
            assert key in info

    def test_labels_match(self):
        assert criteria_info()['labels'] == LABELS

    def test_all_threshold_keys(self):
        th = criteria_info()['thresholds']
        for key in ('quiet', 'normal', 'busy'):
            assert key in th
