"""Runtime S-rarity policies and save-scoped settings for normal card pools."""

import json
from dataclasses import dataclass
from functools import lru_cache


CONFIG_KEY = 'gacha.model.v1'
ANCHOR_GROUP = 'hero_precision_90'
EXPANSION_GROUP = 'hero_precision_70'
ANCHOR_UP_PERCENT = 55


@dataclass(frozen=True)
class Settings:
    mode: int = 0
    legacy_base: float = 1.6
    main_base: float = 1.6
    main_target: float = 2.4
    main_margin: float = 0.005
    anchor_legacy: bool = False
    feedback_strength: float = 1.0
    feedback_bias: float = 0.0

    @classmethod
    def parse(cls, values):
        if not isinstance(values, dict) or values.keys() - cls.__dataclass_fields__.keys():
            raise ValueError('抽卡模型配置包含无效字段')
        settings = cls(**values)
        if type(settings.mode) is not int or not 0 <= settings.mode <= 3:
            raise ValueError('抽卡模型须为 0～3')
        if type(settings.anchor_legacy) is not bool:
            raise ValueError('90 抽旧规则须为开关值')
        for field, lo, hi in (
            ('legacy_base', 0, 100), ('main_base', 0, 100), ('main_target', 0, 100),
            ('main_margin', 0, 100), ('feedback_strength', 0, 1), ('feedback_bias', -100, 100),
        ):
            value = getattr(settings, field)
            if type(value) not in (int, float) or not lo <= value <= hi:
                raise ValueError(f'{field} 须在 {lo}～{hi} 之间')
        if not 0 < settings.main_target + settings.main_margin < 100:
            raise ValueError('2 号综合目标加补正后须大于 0% 且低于 100%')
        if settings.mode >= 2:
            for group in (EXPANSION_GROUP, ANCHOR_GROUP):
                curve_for(settings, group)
        return settings

    @classmethod
    def decode(cls, raw):
        return cls.parse(json.loads(raw)) if raw else cls()

    def corrected_anchor(self, group):
        return group == ANCHOR_GROUP and (self.mode == 1 or (self.mode >= 2 and not self.anchor_legacy))


def long_run_rate(curve, corrected_anchor=False):
    """Renewal rate. Corrected anchor renews on UP and can award off-UP S."""
    alive, draws, hits = 1.0, 0.0, 0.0
    for i, chance in enumerate(curve, 1):
        draws += alive
        hits += alive * chance
        up_weight = ANCHOR_UP_PERCENT / 100 if corrected_anchor and i < len(curve) else 1.0
        alive *= 1.0 - up_weight * chance
    return hits / draws


def curve_bounds(base, cap, flat_until):
    floor = (base,) * (cap - 1) + (1.0,)
    ceiling = (base,) * flat_until + (1.0,) * (cap - flat_until)
    return floor, ceiling


@lru_cache(maxsize=128)
def fit_curve(base, target, cap, flat_until, corrected_anchor):
    def curve(power):
        return (base,) * flat_until + tuple(
            base + (1.0 - base) * (i / (cap - flat_until)) ** power
            for i in range(1, cap - flat_until)
        ) + (1.0,)

    floor, ceiling = curve_bounds(base, cap, flat_until)
    minimum = long_run_rate(floor, corrected_anchor)
    maximum = long_run_rate(ceiling, corrected_anchor)
    if not minimum - 1e-12 <= target <= maximum + 1e-12:
        raise ValueError(f'{cap} 抽档的综合目标须在 {minimum:.4%}～{maximum:.4%} 之间')
    if target <= minimum + 1e-12:
        return floor
    if target >= maximum - 1e-12:
        return ceiling
    low, high = 0.0, 1.0
    while long_run_rate(curve(high), corrected_anchor) > target:
        high *= 2.0
    for _ in range(48):
        mid = (low + high) / 2
        if long_run_rate(curve(mid), corrected_anchor) > target:
            low = mid
        else:
            high = mid
    return curve((low + high) / 2)


def curve_limits(group, corrected_anchor):
    anchor = group == ANCHOR_GROUP
    return (90 if anchor else 70), (80 if corrected_anchor else 65)


def curve_for(settings, group):
    corrected = settings.corrected_anchor(group)
    cap, flat_until = curve_limits(group, corrected)
    return fit_curve(settings.main_base / 100, (settings.main_target + settings.main_margin) / 100,
                     cap, flat_until, corrected)


def main_target_range(main_base, anchor_legacy):
    """Common fitted target interval of the 70- and 90-pull curves, in percent."""
    if not 0 <= main_base <= 100:
        raise ValueError('主表基础出金率须在 0%～100% 之间')
    ranges = []
    for group in (EXPANSION_GROUP, ANCHOR_GROUP):
        corrected = group == ANCHOR_GROUP and not anchor_legacy
        cap, flat_until = curve_limits(group, corrected)
        floor, ceiling = curve_bounds(main_base / 100, cap, flat_until)
        ranges.append((100 * long_run_rate(floor, corrected), 100 * long_run_rate(ceiling, corrected)))
    return max(lower for lower, _ in ranges), min(upper for _, upper in ranges)


def preset_feedback(settings):
    return (settings.main_base, settings.main_target, settings.main_margin) == (1.6, 2.4, 0.005)


def observe_feedback(ema, hit, chance):
    return 0.95 * ema + 0.05 * (hit - chance)


@lru_cache(maxsize=128)
def feedback_profile(group, curve, anchor_legacy, use_preset):
    # Frozen presets measured at 1.60% base, 2.40% nominal and +0.005 pp margin.
    # For a custom main table, retain the gain and use its soft-pity curve directly.
    base = curve[0]
    if group != ANCHOR_GROUP and use_preset:
        return 0.0927734375, tuple(
            (1.0 - base) * (max(0, pull - 60) / 9) ** 14 * 0.8596213062146963
            for pull in range(1, 70)
        )
    strength, scale = (0.0927734375, 1.0)
    if group == ANCHOR_GROUP:
        strength, scale = ((0.630859375, 0.1590372662083531) if anchor_legacy
                           else (0.1123046875, 0.7080247796593427))
    if not use_preset:
        scale = 1.0
    return strength, tuple((chance - base) * scale for chance in curve[:-1])


def feedback_probability(settings, group, curve, pull, ema, override=None):
    """A request override, when present, takes precedence over the saved bias."""
    bias = settings.feedback_bias if override is None else override
    if pull >= len(curve) or bias == 100:
        return 1.0
    if settings.feedback_strength == 0 and bias == 0:
        return curve[pull - 1]
    base = curve[0]
    strength, bonuses = feedback_profile(group, curve, settings.anchor_legacy, preset_feedback(settings))
    gain = 0.5 if ema < 0 else 0.25
    return max(base, min(1.0, base + bonuses[pull - 1] - gain * ema * strength
                         * settings.feedback_strength + bias / 100))
