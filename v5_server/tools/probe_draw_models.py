"""Compare experimental draw models using exact expectations and seeded simulation.

The probe reads the current S-rate function from source without importing the
game server. It simulates a selected target in each pool, without game accounts,
inventory, or database access.
"""

import argparse
import ast
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from random import Random
from statistics import mean, pstdev


TARGET_RATE = 0.024
MAIN_RATE_MARGIN_PP = 0.005
ANCHOR_UP_WEIGHT = 0.55  # Historical anchor disclosure, used as a probe parameter.
EMA_ALPHA = 0.05
GAIN_UP = 0.5
GAIN_DOWN = 0.25
CALIBRATION_DRAWS = 120_000
CALIBRATION_SEEDS = (11777, 20323)
ADAPTIVE_BATCH_DRAWS = 20_000
ADAPTIVE_WARMUP_BATCHES = 20
ADAPTIVE_AVERAGING_BATCHES = 50
ADAPTIVE_INITIAL_GAIN = 120.0
LATE_PITY_70_POWER = 14
MISS, OFF_UP, UP = range(3)
POOL_GROUPS = {
    "expansion": "hero_precision_70",
    "standard": "hero_standard_70",
    "servant": "weapon_servant_70",
    "anchor": "hero_precision_90",
}


@cache
def current_curve(group, base=None):
    path = Path(__file__).resolve().parents[1] / "draw_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    nodes = [node for node in tree.body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id.startswith("BASE_RATE_")
                     for target in node.targets)]
    nodes.append(next(node for node in tree.body if isinstance(node, ast.ClassDef)
                      and node.name == "DrawProbabilityRules"))
    service = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == "DrawService")
    nodes.append(next(node for node in service.body if isinstance(node, ast.FunctionDef)
                      and node.name == "calc_ssr_prob"))
    namespace = {"dataclass": dataclass}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    rules = namespace["DrawProbabilityRules"].default_for(group)
    if base is not None:
        rules = replace(rules, ssr_rate=base)
    return tuple(namespace["calc_ssr_prob"](group, pull, rules=rules)
                 for pull in range(1, rules.validate_for(group) + 1))


@dataclass(frozen=True, slots=True)
class Routing:
    up_weight: float
    off_resets: bool = True

    def up_chance(self, guaranteed, last_pull):
        return 1.0 if guaranteed or (last_pull and not self.off_resets) else self.up_weight


def exact(curve, routing):
    """Expected pulls and off-target results in one cycle ending at the UP."""
    @cache
    def remaining(pity, guaranteed):
        rate = curve[pity]
        up = routing.up_chance(guaranteed, pity == len(curve) - 1)
        off = rate * (1 - up)
        miss = 1 - rate
        draws, extras = 1.0, off
        if miss:
            n, s = remaining(pity + 1, guaranteed)
            draws += miss * n
            extras += miss * s
        if off:
            n, s = remaining(0, True) if routing.off_resets else remaining(pity + 1, False)
            draws += off * n
            extras += off * s
        return draws, extras

    draws, extras = remaining(0, False)
    return (1 + extras) / draws, draws, extras


def fitted_curve(base, cap, flat_until, routing, target):
    def curve(gamma):
        return tuple(base + (1 - base) * ((max(0, pull - flat_until)) /
                     (cap - flat_until)) ** gamma for pull in range(1, cap + 1))

    floor = (base,) * (cap - 1) + (1.0,)
    ceiling = (base,) * flat_until + (1.0,) * (cap - flat_until)
    minimum, maximum = exact(floor, routing)[0], exact(ceiling, routing)[0]
    if not minimum - 1e-12 <= target <= maximum + 1e-12:
        raise ValueError(f"target {target:.3%} is outside {minimum:.3%}..{maximum:.3%}")
    if target <= minimum + 1e-12:
        return floor, float("inf")
    if target >= maximum - 1e-12:
        return ceiling, 0.0
    low, high = 0.0, 1.0
    while exact(curve(high), routing)[0] > target:
        high *= 2
    for _ in range(48):
        gamma = (low + high) / 2
        if exact(curve(gamma), routing)[0] > target:
            low = gamma
        else:
            high = gamma
    gamma = (low + high) / 2
    fitted = curve(gamma)
    assert abs(exact(fitted, routing)[0] - target) < 1e-10
    return fitted, gamma


@dataclass(slots=True)
class State:
    pity: int = 0
    guaranteed: bool = False
    ema: float = TARGET_RATE
    residual_ema: float = 0.0

    def observe(self, hit, chance):
        self.ema = (1 - EMA_ALPHA) * self.ema + EMA_ALPHA * hit
        self.residual_ema = (1 - EMA_ALPHA) * self.residual_ema + EMA_ALPHA * (hit - chance)


@dataclass(frozen=True, slots=True)
class Model:
    label: str
    curve: tuple[float, ...]
    routing: Routing
    feedback: bool = False
    target: float = TARGET_RATE
    feedback_bias: float = 0.0
    feedback_strength: float = 1.0
    residual_feedback: bool = False
    soft_pity_scale: float = 1.0

    def probability(self, state):
        main = self.curve[state.pity]
        if not self.feedback or state.pity == len(self.curve) - 1:
            return main
        # A +100 pp override guarantees S even when inherited feedback is negative.
        if self.feedback_bias == 1.0:
            return 1.0
        error = -state.residual_ema if self.residual_feedback else self.target - state.ema
        gain = GAIN_UP if error > 0 else GAIN_DOWN
        base = self.curve[0]
        soft_pity_bonus = (main - base) * self.soft_pity_scale
        rate = base + soft_pity_bonus + self.feedback_strength * gain * error + self.feedback_bias
        return max(base, min(1.0, rate))

    def draw(self, state, hit_roll, up_roll):
        last_pull = state.pity == len(self.curve) - 1
        chance = self.probability(state)
        result = MISS
        if hit_roll < chance:
            weight = self.routing.up_chance(state.guaranteed, last_pull)
            result = UP if up_roll < weight else OFF_UP

        if result == UP:
            state.pity, state.guaranteed = 0, False
        elif result == OFF_UP and self.routing.off_resets:
            state.pity, state.guaranteed = 0, True
        else:
            state.pity += 1

        # Keep the pre-draw probability even when the result resets pity.
        state.observe(result != MISS, chance)
        return result


def models_for(pool, legacy_base=None, main_target=TARGET_RATE, *, main_base=None,
               feedback_target=None, feedback_strength=1.0):
    group = POOL_GROUPS[pool]
    legacy = current_curve(group, legacy_base)
    if main_base is None:
        main_base = current_curve(group)[0]
    if feedback_target is None:
        feedback_target = main_target
    anchor = pool == "anchor"
    old_routing = Routing(0.5 if pool == "expansion" else 1.0)
    new_routing = Routing(ANCHOR_UP_WEIGHT, off_resets=False) if anchor else old_routing
    specs = [Model("0", legacy, old_routing), Model("1", legacy, new_routing)]
    profiles = [("corrected", new_routing, 80 if anchor else 65)]
    if anchor:
        # Starting after 80 cannot fit 2.40% with the legacy all-UP routing.
        profiles.append(("legacy", old_routing, 65))
    for profile, routing, flat_until in profiles:
        suffix = f"/{profile}" if anchor else ""
        try:
            curve, gamma = fitted_curve(main_base, len(legacy), flat_until, routing, main_target)
        except ValueError as error:
            print(f"{pool}{suffix} modes 2/3 unavailable: {error}")
            continue
        shadow = Model(f"3/raw{suffix}", curve, routing, feedback=True, target=feedback_target,
                       feedback_strength=feedback_strength)
        specs.extend([Model(f"2{suffix}", curve, routing, target=main_target), shadow,
                      replace(shadow, label=f"3/residual{suffix}", residual_feedback=True)])
        print(f"{pool}{suffix} fit: flat through {flat_until}, gamma={gamma:.6f}, "
              f"penultimate S={curve[-2]:.3%}")
    return specs


def calibrate_feedback(model, seeds, *, adaptive_soft_pity=False):
    """Fit feedback using seeds reserved for calibration."""
    base = model.curve[0]
    floor = (base,) * (len(model.curve) - 1) + (1.0,)
    minimum = exact(floor, model.routing)[0]
    if model.target < minimum - 1e-12:
        raise ValueError(f"target {model.target:.3%} is below minimum {minimum:.3%}")
    if model.feedback_strength == 0 and abs(exact(model.curve, model.routing)[0] - model.target) < 1e-12:
        return model
    if model.residual_feedback:
        return calibrate_residual_feedback(model, seeds, minimum, adaptive_soft_pity=adaptive_soft_pity)
    if abs(minimum - model.target) < 1e-12:
        return replace(model, feedback_bias=-1.0)

    return calibrate_feedback_bias(model, seeds)


def late_pity_70_curve(base):
    """Give the final nine non-guaranteed pulls a steep, calibratable ramp."""
    return tuple(base + (1 - base) * (max(0, pull - 60) / 9) ** LATE_PITY_70_POWER
                 for pull in range(1, 70)) + (1.0,)


def calibrate_residual_feedback(model, seeds, minimum, *, adaptive_soft_pity=False):
    if abs(minimum - model.target) < 1e-12:
        return replace(model, feedback_strength=0.0, soft_pity_scale=0.0)

    draws = 2 * CALIBRATION_DRAWS

    def measured_rate(trial):
        return simulate(trial, draws, seeds)[0]

    without_soft_pity = replace(model, soft_pity_scale=0.0)
    # Reserve half of the headroom above hard pity for the soft-pity curve.
    feedback_budget = (minimum + model.target) / 2
    strength = model.feedback_strength
    if strength and measured_rate(without_soft_pity) > feedback_budget:
        low, high = 0.0, strength
        for _ in range(10):
            middle = (low + high) / 2
            trial = replace(without_soft_pity, feedback_strength=middle)
            if measured_rate(trial) <= feedback_budget:
                low = middle
            else:
                high = middle
        strength = low

    model = replace(model, feedback_strength=strength)
    full_rate = measured_rate(model)
    if full_rate < model.target:
        # A higher independent target may need a global increase.
        return calibrate_feedback_bias(model, seeds, minimum_bias=0.0)

    if adaptive_soft_pity:
        return adapt_soft_pity(model, seeds)

    low, high = 0.0, 1.0
    best, best_error = model, full_rate - model.target
    for _ in range(11):
        scale = (low + high) / 2
        trial = replace(model, soft_pity_scale=scale)
        error = measured_rate(trial) - model.target
        if abs(error) < best_error:
            best, best_error = trial, abs(error)
        if error < 0:
            low = scale
        else:
            high = scale
    return best


def adapt_soft_pity(model, seeds):
    """Tune on continuous simulated histories, then freeze the averaged scale."""
    scales = []
    for seed in seeds:
        rng = Random(seed)
        state = State(ema=model.target)
        scale = model.soft_pity_scale
        for index in range(ADAPTIVE_WARMUP_BATCHES + ADAPTIVE_AVERAGING_BATCHES):
            trial = replace(model, soft_pity_scale=scale)
            hits = sum(trial.draw(state, rng.random(), rng.random()) != MISS
                       for _ in range(ADAPTIVE_BATCH_DRAWS))
            if index >= ADAPTIVE_WARMUP_BATCHES:
                scales.append(scale)
            error = model.target - hits / ADAPTIVE_BATCH_DRAWS
            gain = ADAPTIVE_INITIAL_GAIN / max(1, ((index + 1) / ADAPTIVE_WARMUP_BATCHES) ** 0.5)
            scale = max(0.0, min(model.soft_pity_scale, scale + gain * error))
    return replace(model, soft_pity_scale=mean(scales))


def calibrate_feedback_bias(model, seeds, minimum_bias=-2.0):
    low, high = minimum_bias, 2.0
    best = model
    best_error = float("inf")
    for index in range(15):
        bias = 0.0 if index == 0 else (low + high) / 2
        trial = replace(model, feedback_bias=bias)
        rate, *_ = simulate(trial, CALIBRATION_DRAWS, seeds)
        error = rate - model.target
        if abs(error) < best_error:
            best, best_error = trial, abs(error)
        if error > 0:
            high = bias
        else:
            low = bias
    return best


def check_boundaries(expansion, anchor):
    old, fixed, main, shadow = anchor[:4]
    state = State(pity=88)
    assert fixed.draw(state, 0.0, 0.99) == OFF_UP and state.pity == 89
    assert fixed.draw(state, 0.999, 0.99) == UP and state.pity == 0
    state = State(pity=88)
    assert old.draw(state, 0.0, 0.99) == UP and state.pity == 0
    state = State(pity=69)
    assert expansion[0].draw(state, 0.999, 0.99) == OFF_UP
    assert state.pity == 0 and state.guaranteed
    assert expansion[3].draw(state, 0.0, 0.99) == UP and not state.guaranteed

    # Use one live state through 0 -> 1 -> 2 -> 3 -> 0.
    state = State(pity=86)
    observed_ema = state.ema
    for model, hit_roll, outcome in [(old, 0.999, MISS), (fixed, 0.0, OFF_UP),
                                     (main, 0.999, MISS), (shadow, 0.999, UP),
                                     (old, 0.999, MISS)]:
        before = (state.pity, state.guaranteed, state.ema)
        shadow.probability(state)
        assert before == (state.pity, state.guaranteed, state.ema)
        assert model.draw(state, hit_roll, 0.99) == outcome
        observed_ema = (1 - EMA_ALPHA) * observed_ema + EMA_ALPHA * (outcome != MISS)
        assert state.ema == observed_ema
    assert state.pity == 1 and not state.guaranteed

    # Activating feedback uses the latest settled result before the next roll.
    state = State(pity=84)
    assert main.draw(state, 0.0, 0.99) == OFF_UP
    main_rate, feedback_rate = main.probability(state), shadow.probability(state)
    assert feedback_rate < main_rate
    assert shadow.draw(state, (main_rate + feedback_rate) / 2, 0.0) == MISS
    assert state.pity == 86

    state = State(ema=0.0)
    assert shadow.probability(state) > main.probability(state)
    state.ema = 1.0
    assert shadow.probability(state) == shadow.curve[0]
    state.pity = len(shadow.curve) - 2
    assert shadow.curve[0] <= shadow.probability(state) < main.probability(state)
    state.pity += 1
    assert shadow.probability(state) == 1.0

    # Follow each active model's pre-draw probability, then switch to residual feedback.
    residual = anchor[4]
    state = State(pity=84)
    expected = 0.0
    for model, hit_roll, outcome in [(old, 0.999, MISS), (fixed, 0.0, OFF_UP),
                                     (main, 0.999, MISS), (residual, 0.0, OFF_UP),
                                     (residual, 0.999, MISS), (residual, 0.999, UP)]:
        chance = model.probability(state)
        if model is residual:
            assert residual.probability(state) == residual.probability(
                replace(state, ema=0.0, residual_ema=expected))
        if state.pity == 89:
            assert chance == 1.0
        assert model.draw(state, hit_roll, 0.99) == outcome
        expected = (1 - EMA_ALPHA) * expected + EMA_ALPHA * ((outcome != MISS) - chance)
        assert state.residual_ema == expected
    assert state.pity == 0 and not state.guaranteed

    # An all-S ten-pull can start with adverse EMA from any preceding mode.
    for model in (*expansion, *anchor):
        if not model.feedback:
            continue
        forced = replace(model, feedback_bias=1.0)
        state = State(ema=1.0, residual_ema=1.0)
        expected = OFF_UP if model.routing.up_weight < 1 else UP
        assert forced.draw(state, 0.999999, 0.99) == expected
        for _ in range(9):
            assert forced.draw(state, 0.999999, 0.99) != MISS


def simulate(model, draws_per_seed, seeds, *, measure_blocks=False):
    histogram = [0] * (2 * len(model.curve) + 1)
    total_s = total_up = hard_up = 0
    blocks = []
    multi_s_tens = total_tens = 0
    late_s = hard_s = 0
    for seed in seeds:
        rng = Random(seed)
        state = State(ema=model.target)
        last_up = 0
        previous_block_total = total_s
        previous_ten_total = total_s
        for pull in range(1, draws_per_seed + 1):
            pity = state.pity
            at_cap = pity == len(model.curve) - 1
            result = model.draw(state, rng.random(), rng.random())
            total_s += result != MISS
            if measure_blocks and result != MISS:
                late_s += len(model.curve) - 10 <= pity < len(model.curve) - 1
                hard_s += at_cap
            if measure_blocks and pull % 10 == 0:
                multi_s_tens += total_s - previous_ten_total >= 2
                total_tens += 1
                previous_ten_total = total_s
            if measure_blocks and pull % 1000 == 0:
                blocks.append(total_s - previous_block_total)
                previous_block_total = total_s
            if result == UP:
                histogram[pull - last_up] += 1
                last_up = pull
                total_up += 1
                hard_up += at_cap
    n = draws_per_seed * len(seeds)
    mean = sum(pulls * count for pulls, count in enumerate(histogram)) / total_up
    cumulative = 0
    for pulls, count in enumerate(histogram):
        cumulative += count
        if cumulative >= 0.90 * total_up:
            block_sd = pstdev(blocks) if blocks else None
            multi_s_rate = multi_s_tens / total_tens if total_tens else None
            return (total_s / n, mean, pulls, hard_up / total_up, (total_s - total_up) / total_up,
                    block_sd, multi_s_rate, late_s / total_s, hard_s / total_s)


def feedback_at_31(model):
    """Compare two synthetic follower histories at the same pity, before draw 31."""
    chances = []
    for last_hit in (False, True):
        state = State(pity=30, ema=model.target)
        for _ in range(29):
            state.observe(False, model.curve[0])
        state.observe(last_hit, model.curve[0])
        chances.append(model.probability(state))
    return chances


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", choices=[*POOL_GROUPS, "all"], default="all")
    parser.add_argument("--draws", type=int, default=300_000, help="draws per model and seed")
    parser.add_argument("--seeds", type=int, nargs="+", default=[2041, 3109, 5437])
    base_group = parser.add_mutually_exclusive_group()
    base_group.add_argument("--legacy-base-rate", type=float, nargs="+",
                            help="base S rate(s) in percent for modes 0 and 1")
    base_group.add_argument("--base-rate", type=float, nargs="+",
                            help="compatibility sweep of the base S rate in all modes")
    parser.add_argument("--main-base-rate", type=float,
                        help="base S rate in percent for model 2 (model 3 inherits its floor)")
    parser.add_argument("--main-target-rate", "--target-rate", type=float, default=100 * TARGET_RATE,
                        help="nominal long-run S rate in percent for model 2")
    parser.add_argument("--main-rate-margin", type=float, default=MAIN_RATE_MARGIN_PP,
                        help="extra percentage points in model 2's fit (default: %(default)s; 0 for exact target)")
    parser.add_argument("--feedback-target-rate", type=float,
                        help="model 3 target in percent (defaults to model 2's target)")
    parser.add_argument("--feedback-strength", type=float, default=1.0,
                        help="model 3 EMA gain multiplier, from 0 (no EMA adjustment) to 1 (full)")
    bias_group = parser.add_mutually_exclusive_group()
    bias_group.add_argument("--no-feedback-bias", action="store_true",
                            help="run model 3 with zero bias, bypassing feedback calibration")
    bias_group.add_argument("--feedback-bias", type=float, nargs="+",
                            help="fixed model 3 bias(es) in percentage points; +100 guarantees S")
    bias_group.add_argument("--adaptive-soft-pity", action="store_true",
                            help="tune residual feedback's soft pity in batches, then freeze it")
    parser.add_argument("--late-pity-70", action="store_true",
                        help="try a steeper 61–69 ramp in model 3's calibrated 70-pull pools")
    args = parser.parse_args()
    if args.draws < 10_000:
        parser.error("--draws must be at least 10000")
    if len(args.seeds) != len(set(args.seeds)):
        parser.error("validation seeds must be distinct")
    legacy_rates = args.legacy_base_rate or args.base_rate or [None]
    base_rates = [*legacy_rates, args.main_base_rate]
    targets = [args.main_target_rate, args.feedback_target_rate]
    if any(rate is not None and not 0 < rate < 100 for rate in targets) or any(
            rate is not None and not 0 <= rate < 100 for rate in base_rates):
        parser.error("base rate must be in [0, 100), and target rate in (0, 100)")
    if not 0 <= args.main_rate_margin < 100 - args.main_target_rate:
        parser.error("model 2 margin must be nonnegative and keep the fitted target below 100%")
    if not 0 <= args.feedback_strength <= 1:
        parser.error("feedback strength must be in [0, 1]")
    if args.feedback_bias and any(not -100 <= bias <= 100 for bias in args.feedback_bias):
        parser.error("fixed feedback bias must be in [-100, 100] percentage points")
    pools = POOL_GROUPS if args.pool == "all" else [args.pool]
    if args.late_pity_70 and (args.pool == "anchor" or args.no_feedback_bias or args.feedback_bias is not None):
        parser.error("--late-pity-70 requires a 70-pull pool and automatic calibration")
    check_boundaries(models_for("expansion"), models_for("anchor"))
    validation_seeds = set(args.seeds)
    calibration_seeds = []
    for seed in CALIBRATION_SEEDS:
        while seed in validation_seeds or seed in calibration_seeds:
            seed += 1
        calibration_seeds.append(seed)
    fixed_biases = [0.0] if args.no_feedback_bias else args.feedback_bias
    calibration = fixed_biases is None
    feedback_summary = (f"Calibration seeds={calibration_seeds}." if calibration else
                        f"Fixed feedback biases (pp): {fixed_biases}.")
    print(f"Boundary and mode-switch checks passed. Validation: {args.draws} draws per seed, "
          f"seeds={args.seeds}. {feedback_summary}")
    if args.adaptive_soft_pity:
        print(f"Adaptive soft pity: {ADAPTIVE_BATCH_DRAWS} draws per batch, "
              f"{ADAPTIVE_WARMUP_BATCHES} warmup + {ADAPTIVE_AVERAGING_BATCHES} averaging batches per seed.")
    main_target = (args.main_target_rate + args.main_rate_margin) / 100
    feedback_target = (args.main_target_rate if args.feedback_target_rate is None
                       else args.feedback_target_rate) / 100
    for percent in legacy_rates:
        legacy_base = None if percent is None else percent / 100
        main_base = legacy_base if args.base_rate else None
        if args.main_base_rate is not None:
            main_base = args.main_base_rate / 100
        specs = {pool: models_for(pool, legacy_base, main_target, main_base=main_base,
                                 feedback_target=feedback_target,
                                 feedback_strength=args.feedback_strength) for pool in pools}
        if args.late_pity_70:
            for models in specs.values():
                for index, model in enumerate(models):
                    if model.residual_feedback and len(model.curve) == 70:
                        models[index] = replace(model, label=f"{model.label}/late",
                                                curve=late_pity_70_curve(model.curve[0]))
        legacy_display = "source default" if legacy_base is None else f"{legacy_base:.3%}"
        main_display = "source default" if main_base is None else f"{main_base:.3%}"
        print(f"\n0/1 base={legacy_display}; 2 base={main_display}, target={main_target:.3%} "
              f"({args.main_target_rate:.3f}% nominal + {args.main_rate_margin:.3f} pp); "
              f"3 target={feedback_target:.3%}, feedback strength={args.feedback_strength:g}")
        if fixed_biases is not None:
            specs = {pool: [trial for model in models for trial in (
                [replace(model, label=f"{model.label}@{bias:+g}", feedback_bias=bias / 100)
                 for bias in fixed_biases] if model.feedback else [model]
            )] for pool, models in specs.items()}
        run_comparison(specs, args.draws, args.seeds, calibration_seeds if calibration else None,
                       adaptive_soft_pity=args.adaptive_soft_pity)


def run_comparison(specs, draws, seeds, calibration_seeds, *, adaptive_soft_pity=False):
    print("Pool       Model                         S exact    S MC   vs target    UP MC   UP P90  UP on hard  Off/UP  S/1k SD  2+ S/ten    S before cap    S on cap")
    for pool, models in specs.items():
        for model in models:
            if model.feedback:
                if calibration_seeds is not None:
                    try:
                        model = calibrate_feedback(model, calibration_seeds,
                                                   adaptive_soft_pity=adaptive_soft_pity)
                    except ValueError as error:
                        print(f"  {pool}/{model.label} unavailable: {error}")
                        continue
                dry, recent_hit = feedback_at_31(model)
                print(f"  {pool}/{model.label} feedback offset={100 * model.feedback_bias:+.4f} pp; "
                      f"strength={model.feedback_strength:.4f}; soft pity scale={model.soft_pity_scale:.4f}; "
                      f"draw 31 at same pity: dry={dry:.4%}, recent S={recent_hit:.4%}, "
                      f"gap={100 * (dry - recent_hit):.4f} pp (synthetic histories)")
            sample_rate, sample_up, p90, hard, off, block_sd, multi_s_rate, late_s, hard_s = simulate(
                model, draws, seeds, measure_blocks=True)
            if model.feedback:
                rate_display = "--"
            else:
                rate, up, _ = exact(model.curve, model.routing)
                rate_display = f"{rate:.3%}"
                scale = (3 * 300_000 / (len(seeds) * draws)) ** 0.5
                assert abs(sample_rate - rate) < 0.0012 * scale, (pool, model.label, sample_rate, rate)
                assert abs(sample_up - up) < 1.8 * scale, (pool, model.label, sample_up, up)
            delta = "--" if model.label in ("0", "1") else f"{100 * (sample_rate - model.target):+.3f} pp"
            print(f"{pool:10} {model.label:28} {rate_display:>8} {sample_rate:>8.3%} "
                  f"{delta:>10} {sample_up:>8.2f} {p90:>8} {hard:>11.2%} {off:>7.3f} "
                  f"{block_sd:>8.2f} {multi_s_rate:>9.3%} {late_s:>15.2%} {hard_s:>11.2%}")


if __name__ == "__main__":
    main()
