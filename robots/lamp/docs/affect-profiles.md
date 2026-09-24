# Affect profiles for tracking

This first scaffolding pass adds an affect state to the existing tracker. The tracker still chooses and follows the same target with the same control law; affect changes how its SmoothDamp follower responds. The default is `neutral`, which preserves the existing tracking tuning exactly.

The implementation lives in `hal/drivers/tracking/affect.py` (`AffectState`) and is integrated into `TrackerService`. See [Vision tracking](vision-tracking.md) for the underlying controller.

## Relative profiles

The profiles are `neutral`, `curious`, `calm`, `happy`, `sad`, `excited`, and `fearful`. Each profile supplies relative factors for `smooth_time` and `max_speed`, rather than copying the baseline pursuit and saccade settings. `neutral` uses identity factors (1.0).

On each tracking tick, the service resolves the current baseline constants for the selected pursuit or saccade mode, applies the affect factors, and then applies the safety speed cap. Future tuning of the baseline therefore cascades to every profile. Affect cannot raise the resulting speed above that cap.

```text
Current pursuit/saccade constants
    → relative affect factors
    → safety speed cap
    → existing SmoothDamp follower
```

The named profiles are experimental starting points, not physically calibrated GL40 II motor settings. This change does not modify motor calibration or require hardware motion to test.

## State and transitions

Intensity ranges from `0.0` to `1.0`: zero blends the profile back to neutral, and one applies its full relative factors. Intermediate values interpolate between those endpoints.

Changes use a monotonic clock and a smoothstep transition. They blend the motion factors over the requested duration instead of abruptly replacing them. A new change during a transition starts from the current blended factors.

The internal Python entry point is:

```python
tracker_service.set_affect(name, intensity=1.0, transition_s=0.5)
```

For example, an existing `TrackerService` instance can request `"curious"` at partial intensity, then return to `"neutral"`. This setter configures affect; it is not a command to start tracking or move a motor. Tracking status includes an `affect` entry so callers can inspect the state.

## Scope of this pass

There is no automatic binding to `/emotion`. A separate HTTP setter and simulator selector configure tracking affect. The existing emotion expressions remain separate from this tracking state.

Attention selection is also unchanged: this pass does not implement person → face → eye priorities or wave interruptions. Curious approach/nudge and fearful retreat/recoil are future behavior decisions, not consequences of the motion factors added here.

This is a repository scaffolding change only. No service restart or deployment is part of this pass.

### Prototype controls

The built-in simulator now exposes `/servo/affect` styles: neutral (1,1), curious (0.85,1), calm (1.35,0.7), happy (0.9,1.05), sad (1.5,0.55), excited (0.75,1.15), fearful (0.65,1.25), listed as smoothing-time and speed multipliers. These are experimental tracking dynamics, not calibrated pose offsets. Selecting one does not move or resume a motor or start tracking. Existing `/emotion` recordings remain separate. `/servo/output` exposes current affect and Pi joint state with explicit mock/driver provenance.
