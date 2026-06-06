"""Protocol address and function resolver for restricted Agent modules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AddressConfig:
    """Configurable protocol values that may differ across controller versions."""

    cartesian_current: int = 1500
    cartesian_feedback: int = 1612
    safe_speed_max: int = 1708
    safe_acc_max: int = 1710
    safe_dec_max: int = 1712
    pose_upper_angle: int = 1732
    pose_lower_angle: int = 1734
    pose_cw_angle: int = 1736
    pose_ccw_angle: int = 1738
    continuous_path_func: int = 112
    absolute_motion_func: int = 108
    any_axis_moving_bit: int | None = None


class AddressResolver:
    """Expose protocol values by semantic name instead of scattering constants."""

    def __init__(self, config: AddressConfig | None = None) -> None:
        self.config = config or AddressConfig()

    @property
    def cartesian_current(self) -> int:
        return int(self.config.cartesian_current)

    @property
    def cartesian_feedback(self) -> int:
        return int(self.config.cartesian_feedback)

    @property
    def safe_speed_max(self) -> int:
        return int(self.config.safe_speed_max)

    @property
    def safe_acc_max(self) -> int:
        return int(self.config.safe_acc_max)

    @property
    def safe_dec_max(self) -> int:
        return int(self.config.safe_dec_max)

    @property
    def pose_upper_angle(self) -> int:
        return int(self.config.pose_upper_angle)

    @property
    def pose_lower_angle(self) -> int:
        return int(self.config.pose_lower_angle)

    @property
    def pose_cw_angle(self) -> int:
        return int(self.config.pose_cw_angle)

    @property
    def pose_ccw_angle(self) -> int:
        return int(self.config.pose_ccw_angle)

    @property
    def continuous_path_func(self) -> int:
        return int(self.config.continuous_path_func)

    @property
    def absolute_motion_func(self) -> int:
        return int(self.config.absolute_motion_func)

    @property
    def any_axis_moving_bit(self) -> int | None:
        if self.config.any_axis_moving_bit is None:
            return None
        return int(self.config.any_axis_moving_bit)
